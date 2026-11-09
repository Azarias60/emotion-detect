# -*-coding: utf-8 -*-
"""
    @Author : Pan
    @E-mail : 390737991@qq.com
    @Date   : 2021-08-03 17:39:23
"""
import os
import argparse
import time

import numpy as np
import cv2
import torch
from basetrainer.utils import setup_config
from pybaseutils import image_utils, coords_utils

from .classifier import inference
from .libs.detector import Detector

# 本子工程资源根目录（config/model/class_name 均按绝对路径解析，不依赖启动 cwd）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Predictor(inference.Inference):
    def __init__(self, cfg):
        super(Predictor, self).__init__(cfg)
        # 移除self.cap（改为动态传入视频源）
        self.detector = Detector(detect_type="face", prb_thresh=0.85, iou_thresh=0.2)
        self.stop_flag = False
        self.video_writer = None
        self.emotion_log = []  # 记录每次预测情绪的时间戳和情绪名
        self.class_name = ['angry', 'disgust', 'fear', 'happy', 'neutral', 'sad', 'surprise']

    def detect_face(self, rgb_image):
        """进行人脸检测:使用检测器检测图像中的人脸，返回检测到的人脸和边界框"""
        dets, labels = self.detector.detect(rgb_image)  # dets is bbox_score
        faces = []
        if len(dets) > 0:
            boxes = dets[:, 0:4]
            boxes = coords_utils.extend_xyxy(boxes, scale=[1.1, 1.1])
            faces = image_utils.get_bboxes_crop(rgb_image, boxes)
        return faces, dets

    def predict(self, faces):
        """分类预测:返回预测的索引和分数"""
        pred_index, pred_score = np.zeros((0,)), np.zeros((0,))
        if len(faces) > 0:
            pred_index, pred_score = self.inference(faces)
        return pred_index, pred_score

    def task(self, image):
        faces, dets = self.detect_face(image)
        pred_index, pred_score = self.predict(faces)
        timestamp = time.time()
        if len(pred_index) > 0:
            for idx, score in zip(pred_index, pred_score):
                emotion = self.class_name[int(idx)]
                # 优化日志格式：包含置信度，方便接口返回
                self.emotion_log.append({
                    "timestamp": round(timestamp, 3),
                    "emotion": emotion,
                    "confidence": round(float(score), 3)
                })
        return pred_index, pred_score, dets

    def start_capture(self, video_file, save_video=None, interval=1, vis=False, label=None):
        """
        start capture video（移除PyQt依赖，适配无GUI环境）
        :param video_file: *.avi,*.mp4,... 视频文件或摄像头ID
        :param save_video: *.avi 保存视频文件路径
        :param interval: 视频抽帧间隔
        :param vis: 是否显示检测结果（云端设为False）
        :param label: QLabel对象（弃用，保留参数兼容）
        """
        self.stop_flag = False
        # 动态初始化视频源（兼容文件/摄像头）
        video_cap = cv2.VideoCapture(video_file)
        if not video_cap.isOpened():
            raise ValueError(f"无法打开视频源: {video_file}")

        width = int(video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = int(video_cap.get(cv2.CAP_PROP_FPS))

        if save_video:
            self.video_writer = image_utils.get_video_writer(save_video, width, height, fps)

        count = 0
        while True:
            if self.stop_flag:
                break
            if count % interval == 0:
                video_cap.set(cv2.CAP_PROP_POS_FRAMES, count)
                isSuccess, frame = video_cap.read()
                if not isSuccess:
                    break
                frame = cv2.flip(frame, 1)
                image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pred_index, pred_score, dets = self.task(image)
                if vis or save_video:
                    try:
                        image = self.show_image(image, dets, pred_score, pred_index, use_rgb=True, vis=vis, delay=10)
                        if save_video and self.video_writer:
                            frame = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                            self.video_writer.write(frame)
                    except Exception:
                        pass
            count += 1

        # 释放资源
        video_cap.release()
        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None

    def stop_capture(self):
        self.stop_flag = True

    def show_image(self, image, dets, pred_score, pred_index, use_rgb=True, delay=0, vis=True):
        try:
            image = image_utils.draw_image_detection_bboxes(image, dets, pred_score, pred_index,
                                                            class_name=self.class_name, fontScale=-1)
        except Exception:
            pass
        if vis:
            try:
                image_utils.cv_show_image("det-result", image, use_rgb=use_rgb, delay=delay)
            except Exception:
                pass
        return image

    def reset_log(self):
        """重置情绪日志，避免多请求数据污染"""
        self.emotion_log = []


def get_parser():
    # 配置文件
    config_file = os.path.join(_BASE_DIR, "configs", "config.yaml")
    # 模型文件
    model_file = os.path.join(
        _BASE_DIR, "data", "pretrained", "mobilenet_v2_1.0_CrossEntropyLoss_20230313090258",
        "model", "latest_model_099_94.7200.pth",
    )
    # 默认值改为空（强制指定视频文件，避免默认调用摄像头）
    video_file = ""
    parser = argparse.ArgumentParser(description="Inference Argument")
    parser.add_argument("-c", "--config_file", help="configs file", default=config_file, type=str)
    parser.add_argument("-m", "--model_file", help="model_file", default=model_file, type=str)
    parser.add_argument("--device", help="cuda device id", default="cuda:0", type=str)
    parser.add_argument('--video_file', type=str, default=video_file, help='视频文件路径（必填）或摄像头ID')
    parser.add_argument('--out_dir', type=str, default="output", help='save det result image')
    return parser


def create_predictor():
    """简化Predictor创建逻辑，适配FastAPI调用"""
    parser = get_parser()
    args = parser.parse_args(args=[])  # 跳过命令行参数，避免冲突
    cfg = setup_config.parser_config(args, cfg_updata=False)
    # config.yaml 内的 class_name 为相对路径，统一切换为绝对路径
    if cfg.get("class_name") and not os.path.isabs(str(cfg.class_name)):
        cfg.class_name = os.path.join(_BASE_DIR, str(cfg.class_name))
    # 用 torch 而非 cv2.cuda 判断设备：opencv-python 发行版通常无 CUDA 支持，
    # 旧判断在无 cv2.cuda 属性时会抛异常、在本机则误判为 CPU
    cfg.device = "cuda:0" if torch.cuda.is_available() else "cpu"
    predictor = Predictor(cfg)
    return predictor, cfg