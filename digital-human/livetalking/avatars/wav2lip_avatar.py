###############################################################################
#  Copyright (C) 2024 LiveTalking@lipku https://github.com/lipku/LiveTalking
#  email: lipku@foxmail.com
# 
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  
#       http://www.apache.org/licenses/LICENSE-2.0
# 
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################
#
#  Wav2Lip 数字人 — 迁移自 lipreal.py + lipasr.py
#

import math
import torch
import numpy as np
from pathlib import Path

import os
import time
import cv2
import glob
import pickle
import copy

import queue
from queue import Queue
from threading import Thread, Event
import torch.multiprocessing as mp

from avatars.audio_features.mel import MelASR
import asyncio
from av import AudioFrame, VideoFrame
from avatars.wav2lip.models import Wav2Lip
from avatars.base_avatar import BaseAvatar

from tqdm import tqdm
from utils.logger import logger
from utils.image import mirror_index, lazy_read_imgs
from utils.device import initialize_device
from registry import register

device = initialize_device()
logger.info('Using {} for inference.'.format(device))
PROJECT_ROOT = Path(__file__).resolve().parents[1]

def _load(checkpoint_path):
    if device == 'cuda':
        checkpoint = torch.load(checkpoint_path)
    else:
        checkpoint = torch.load(checkpoint_path,
                                map_location=lambda storage, loc: storage)
    return checkpoint

def load_model(path):
    model = Wav2Lip()
    logger.info("Load checkpoint from: {}".format(path))
    checkpoint = _load(path)
    s = checkpoint["state_dict"]
    new_s = {}
    for k, v in s.items():
        new_s[k.replace('module.', '')] = v
    model.load_state_dict(new_s)

    model = model.to(device)
    return model.eval()

def load_avatar(avatar_id, frame_cache_size=1600, face_cache_size=1024):
    """加载形象的帧列表。

    缓存默认值要和 `config.py` 的 `--frame_cache_size` / `--face_cache_size` 一致。
    512 帧（旧默认）装不下 my_avatar 的热集 —— 3 个待机 710 + talk 225 + talk2 200
    = 1135 帧，于是切段时帧被 LRU 挤出去，渲染线程只能自己同步读盘
    （一帧 720x1280 PNG ≈ 11ms，而 25fps 每帧预算只有 40ms）→ 画面卡顿。
    """
    avatar_path = PROJECT_ROOT / "data" / "avatars" / avatar_id
    full_imgs_path = avatar_path / "full_imgs"
    face_imgs_path = avatar_path / "face_imgs"
    coords_path = avatar_path / "coords.pkl"

    for required in (full_imgs_path, face_imgs_path, coords_path):
        if not required.exists():
            raise FileNotFoundError(f"avatar asset not found: {required}")
    
    with open(coords_path, 'rb') as f:
        coord_list_cycle = pickle.load(f)
    frame_list_cycle = None
    input_img_list = glob.glob(os.path.join(str(full_imgs_path), '*.[jpJP][pnPN]*[gG]'))
    input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    frame_list_cycle = lazy_read_imgs(
        input_img_list, cache_size=max(80, int(frame_cache_size))
    )
    input_face_list = glob.glob(os.path.join(str(face_imgs_path), '*.[jpJP][pnPN]*[gG]'))
    input_face_list = sorted(input_face_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    face_list_cycle = lazy_read_imgs(
        input_face_list, cache_size=max(80, int(face_cache_size))
    )

    return frame_list_cycle,face_list_cycle,coord_list_cycle

@torch.no_grad()
def warm_up(batch_size,model,modelres):
    # 预热函数
    logger.info('warmup model...')
    img_batch = torch.ones(batch_size, 6, modelres, modelres).to(device)
    mel_batch = torch.ones(batch_size, 1, 80, 16).to(device)
    model(mel_batch, img_batch)

@register("avatar", "wav2lip")
class LipReal(BaseAvatar):
    @torch.no_grad()
    def __init__(self, opt, model, avatar):
        super().__init__(opt)

        #self.fps = opt.fps # 20 ms per frame
        
        # self.batch_size = opt.batch_size
        # self.idx = 0
        # self.res_frame_queue = Queue(self.batch_size*2)
        self.model = model

        self.frame_list_cycle,self.face_list_cycle,self.coord_list_cycle = avatar

        self.asr = MelASR(opt,self)
        self.asr.warm_up()
    
    def inference_batch(self, index, audiofeat_batch):
        if hasattr(self, 'action_start') and self.custom_audiotype > 0:
            length = self.action_end - self.action_start + 1
            offset = self.action_start
            is_talk = getattr(self, '_in_talk', False)
        else:
            length = len(self.face_list_cycle)
            offset = 0
            is_talk = True
        img_batch = []
        face_size = self.face_list_cycle[0].shape[0]  # native face_imgs size (256x256)
        for i in range(self.batch_size):
            if is_talk and not hasattr(self, 'action_start'):
                idx = offset + mirror_index(length, index + i)
            elif is_talk:
                idx = offset + ((index + i) % length)
            else:
                # 非 talk 模式：clamp 防止回绕
                pos = index + i
                idx = offset + (pos if pos < length else length - 1)
            face = self.face_list_cycle[idx]
            img_batch.append(face)
        img_batch, audiofeat_batch = np.asarray(img_batch), np.asarray(audiofeat_batch)

        img_masked = img_batch.copy()
        img_masked[:, img_batch.shape[1]//2:] = 0  # mask lower half

        img_batch = np.concatenate((img_masked, img_batch), axis=3) / 255.
        audiofeat_batch = np.reshape(audiofeat_batch, [len(audiofeat_batch), audiofeat_batch.shape[1], audiofeat_batch.shape[2], 1])

        img_batch = torch.FloatTensor(np.transpose(img_batch, (0, 3, 1, 2))).to(device)
        audiofeat_batch = torch.FloatTensor(np.transpose(audiofeat_batch, (0, 3, 1, 2))).to(device)

        with torch.no_grad():
            pred = self.model(audiofeat_batch, img_batch)
        pred = pred.cpu().numpy().transpose(0, 2, 3, 1) * 255.
        return pred

    def _smooth_to_bbox(self, pred_frame, target_w, target_h):
        """Smooth Wav2Lip output and resize to target bbox size.

        The original LiveTalking stores face_imgs at 96×96, runs model at 96×96,
        then UPSCALES to bbox size (~210×143).  The upscale naturally smooths out
        Wav2Lip texture artifacts.

        Our model runs at 256×256, so a direct 256→bbox resize PRESERVES
        artifacts.  To match the original behaviour we shrink to 96×96 first
        (anti-aliased downscale), then upscale to bbox size with bicubic
        interpolation — producing the same smooth, natural look.
        """
        small = cv2.resize(pred_frame.astype(np.uint8), (96, 96),
                           interpolation=cv2.INTER_AREA)
        return cv2.resize(small, (target_w, target_h),
                          interpolation=cv2.INTER_CUBIC)

    def paste_back_frame(self, pred_frame, idx: int):
        """Paste Wav2Lip output onto frame at given index.

        Smooths the model output (256→96→bbox) to match the original LiveTalking
        behaviour, then pastes directly onto the frame at the bbox location.
        """
        bbox = self.coord_list_cycle[idx]
        y1, y2, x1, x2 = bbox
        res_frame = self._smooth_to_bbox(pred_frame, x2 - x1, y2 - y1)
        combine_frame = self.frame_list_cycle[idx].copy()
        combine_frame[y1:y2, x1:x2] = res_frame
        return combine_frame
