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
#  Avatar 基类 — 合并自 basereal.py，集成到 Async Pipeline
#

import math
from numpy.typing import NDArray
import torch
import numpy as np
import subprocess
import os
import time
import cv2
import glob
import resampy
import queue
from queue import Queue
from threading import Thread, Event
from io import BytesIO
import soundfile as sf
import asyncio
from enum import Enum
import json
import importlib
import registry

import torch.multiprocessing as mp
from dataclasses import dataclass, field

from av import AudioFrame, VideoFrame
from fractions import Fraction

from utils.logger import logger
from utils.image import read_imgs,mirror_index
from services.llm_memory import ConversationMemory

# class State(Enum):
#     INIT=0
#     WAIT=1
#     QUESTION=2
#     ANSWER=3

@dataclass
class AudioFrameData:
    data: NDArray[np.float32]
    type: int = 0  # 默认值
    userdata: dict = field(default_factory=dict)

class BaseAvatar:
    def __init__(self, opt):
        self.opt = opt
        self.sample_rate = 16000
        self.chunk = self.sample_rate // (opt.fps*2) # 320 samples per chunk (20ms)
        self.sessionid = self.opt.sessionid

        self.speaking = False
        self.width = self.height = 0
        # 回复代际：flush_talk（打断）时 +1。旧回复的 LLM 线程不会被杀死，
        # 它后续送进来的段落带着旧代际，TTS 消费时据此丢弃，避免新旧回复
        # 的声音叠在一起播（重叠/重复）。
        self._reply_epoch = 0
        # 会话级多轮记忆：让数字人记得住前面聊过什么。每个会话一份，
        # 会话销毁随之回收，不会串到别的会话。
        self.llm_memory = ConversationMemory()
        # 首音延迟分解（诊断用）：回复开始 → 首段入 TTS → 首帧音频上屏
        self._reply_started_at: float | None = None
        self._first_segment_at: float | None = None
        self._first_audio_logged = False

        self.custom_audiotype = 0 # 0: normal, 1: sinlence, >1: custom audio
        self.custom_img_cycle = {}
        self.custom_audio_cycle = {}
        self.custom_audio_index = {}
        self.custom_index = {}
        # self.custom_opt = {}
        self.__loadcustom()

        self.batch_size = opt.batch_size
        # 渲染结果队列深度 = 一个推理批（而不是两个）。队列里每一帧都是
        # "已经算完但还没上屏"的画面，深度直接换算成用户可感知的延迟
        # （batch_size=8 时，16 帧 = 640ms）。推理实测 120~144fps、消费端
        # 只要 25fps，留一批足够吸收抖动，不必留两批。
        self.res_frame_queue = Queue(max(2, int(opt.batch_size)))
        self.render_event = Event()

        _tts_modules = {
            'cosyvoice3': 'tts.cosyvoice3',
        }

        if opt.tts in _tts_modules:
            importlib.import_module(_tts_modules[opt.tts])
            self.tts = registry.create("tts", opt.tts, opt=opt, parent=self)
        else:
            raise ValueError(
                f"TTS module {opt.tts!r} is not supported. "
                f"Available: {sorted(_tts_modules)}"
            )

        _output_modules = {
            'webrtc': 'streamout.webrtc',
            'rtcpush': 'streamout.webrtc',
            'rtmp': 'streamout.rtmp',
            'virtualcam': 'streamout.virtualcam',
        }

        # 初始化 Output 模块
        if opt.transport in _output_modules:
            try:
                importlib.import_module(_output_modules[opt.transport])
                self.output = registry.create("streamout", opt.transport, opt=opt, parent=self)
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    f"Output transport module {_output_modules[opt.transport]} not found"
                ) from exc
        else:
            raise ValueError(
                f"Output transport {opt.transport!r} is not supported. "
                f"Available: {sorted(_output_modules)}"
            )

    def begin_reply(self) -> int:
        """取本次回复的代际令牌，供该回复的每个段落随行携带。

        必须在**回复开始时**调用一次，并把返回值写进每个 ``put_msg_txt``
        的 ``datainfo['reply_epoch']``。若改由 ``put_msg_txt`` 在入队时现取
        ``self._reply_epoch``，被 ``flush_talk()`` 打断的旧回复线程在打断
        *之后* 送来的段落会盖上打断后的新代际，于是判定"不过期"照念——
        新旧两轮回复的语音就叠在一起（听感：回复重复、一直在说、互相重叠）。
        """
        self._reply_started_at = time.monotonic()
        self._first_segment_at = None
        self._first_audio_logged = False
        return self._reply_epoch

    def reply_token_is_current(self, token: int | None) -> bool:
        """该代际令牌是否仍是当前回复（False = 已被打断，应停止供段）。"""
        if token is None:
            return True
        return token >= self._reply_epoch

    def begin_new_turn(self):
        """开一轮新的用户回合：打断在播语音，返回 ``should_stop`` 回调。

        HTTP ``/human`` 的 chat 路径（实时口型页的文字输入框走的就是这条）
        没有 WebSocket 的 generation 过滤，前端也不保证带 ``interrupt``；
        少了这一步，两次输入触发的两轮 ``llm_response`` 会**同时**往同一条
        TTS 队列灌段落，听感就是"回复重复、一直在说、互相重叠"。

        返回值给 ``llm_response(should_stop=...)`` 用：上一轮回复发现自己的
        代际已经不是最新，就在下一个 chunk 处停手，不再白跑完一整轮。
        """
        self.flush_talk()
        token = self._reply_epoch

        def should_stop() -> bool:
            return not self.reply_token_is_current(token)

        return should_stop

    # 如果系统没有使用 pipeline，或者为了向后兼容原来的 ttsreal.py
    def put_msg_txt(self, msg, datainfo:dict | None = None):
        if hasattr(self, 'tts'):
            if self._first_segment_at is None:
                self._first_segment_at = time.monotonic()
            info = dict(datainfo or {})
            # 未显式带代际的调用方（HTTP 一次性播报等）按当前代际盖章；
            # 长回复必须由调用方传入 begin_reply() 的令牌，见其说明。
            info.setdefault('reply_epoch', self._reply_epoch)
            self.tts.put_msg_txt(msg, info)
    
    def put_audio_frame(self, audio_chunk:NDArray[np.float32], datainfo:dict | None = None): # 16khz 20ms pcm
        datainfo = datainfo or {}
        if getattr(self, '_hold_audio', False):
            if not hasattr(self, '_audio_buffer'):
                self._audio_buffer = []
            self._audio_buffer.append((audio_chunk.copy(), dict(datainfo)))
        elif hasattr(self, 'asr'):
            if hasattr(self, '_audio_buffer') and self._audio_buffer:
                self._flush_audio_buffer()
            self.asr.put_audio_frame(audio_chunk, datainfo)

    def _flush_audio_buffer(self):
        """释放缓存的音频帧到 ASR"""
        if hasattr(self, '_audio_buffer') and self._audio_buffer:
            buf = self._audio_buffer
            self._audio_buffer = []
            for chunk, info in buf:
                self.asr.put_audio_frame(chunk, info)
            logger.info(
                "[Audio] released %d buffered frames (%.1fs)",
                len(buf),
                len(buf) * self.chunk / self.sample_rate,
            )

    def release_audio(self):
        """停止缓冲音频并立即释放已缓存的音频"""
        if getattr(self, '_hold_audio', False):
            self._hold_audio = False
            self._flush_audio_buffer()

    def put_audio_file(self, filebyte, datainfo:dict | None = None):
        datainfo = datainfo or {}
        input_stream = BytesIO(filebyte)
        stream = self.__create_bytes_stream(input_stream)
        streamlen = stream.shape[0]
        idx = 0
        first = True
        while streamlen >= self.chunk:
            eventpoint = {}
            if first:
                eventpoint = {'status': 'start'}
                first = False
            if streamlen - self.chunk < self.chunk:
                eventpoint = {'status': 'end'}
            eventpoint.update(**datainfo) 
            self.put_audio_frame(stream[idx:idx+self.chunk], eventpoint)
            streamlen -= self.chunk
            idx += self.chunk

    def put_audio_filepath(self, filepath, datainfo:dict | None = None):
        datainfo = datainfo or {}
        stream = self.__create_bytes_stream(filepath)
        streamlen = stream.shape[0]
        idx = 0
        first = True
        while streamlen >= self.chunk:
            eventpoint = {}
            if first:
                eventpoint = {'status': 'start'}
                first = False
            if streamlen - self.chunk < self.chunk:
                eventpoint = {'status': 'end'}
            eventpoint.update(**datainfo) 
            self.put_audio_frame(stream[idx:idx+self.chunk], eventpoint)
            streamlen -= self.chunk
            idx += self.chunk
    
    def __create_bytes_stream(self, byte_stream):
        stream, sample_rate = sf.read(byte_stream) # [T*sample_rate,] float64
        logger.info(f'[INFO]put audio stream {sample_rate}: {stream.shape}')
        stream = stream.astype(np.float32)

        if stream.ndim > 1:
            logger.info(f'[WARN] audio has {stream.shape[1]} channels, only use the first.')
            stream = stream[:, 0]
    
        if sample_rate != self.sample_rate and stream.shape[0] > 0:
            logger.info(f'[WARN] audio sample rate is {sample_rate}, resampling into {self.sample_rate}.')
            stream = resampy.resample(x=stream, sr_orig=sample_rate, sr_new=self.sample_rate)

        return stream

    def flush_talk(self):
        # 先升代际再清队列：旧回复线程后续入队的段落全部过期
        self._reply_epoch = getattr(self, '_reply_epoch', 0) + 1
        if hasattr(self, 'tts') and hasattr(self.tts, 'flush_talk'):
            self.tts.flush_talk()
        if hasattr(self, 'asr') and hasattr(self.asr, 'flush_talk'):
            self.asr.flush_talk()
        self._audio_buffer = []
        self._hold_audio = False
        self._pending_action = None
        self._speech_started = False
        activate_state = getattr(self, '_activate_state', None)
        if activate_state is not None:
            # 待机段数量按素材实际段表算：单人段表可能只有 2 段待机
            # （yy 只有 idle1 / idle1_final），写死 (1,2,3) 会切到不存在的段
            idle_count = len(getattr(self, 'idle_ranges', None) or (1, 2, 3))
            current_state = getattr(self, '_current_state', None)
            idle_states = [
                state for state in range(1, idle_count + 1) if state != current_state
            ]
            activate_state(idle_states[0] if idle_states else 1)
        else:
            self.custom_audiotype = 0

    # def flush(self):
    #     self.flush_talk()

    def is_speaking(self) -> bool:
        # Also check TTS state: audio may be queued but not yet rendered
        tts_busy = getattr(getattr(self, 'tts', None), 'busy', False)
        return self.speaking or tts_busy
    
    def __loadcustom(self):
        if not hasattr(self.opt, 'customopt') or not self.opt.customopt:
            return
        for item in self.opt.customopt:
            logger.info(item)
            input_img_list = glob.glob(os.path.join(item['imgpath'], '*.[jpJP][pnPN]*[gG]'))
            input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
            self.custom_img_cycle[item['audiotype']] = read_imgs(input_img_list)
            if item.get('audiopath'):
                self.custom_audio_cycle[item['audiotype']], sample_rate = sf.read(item['audiopath'], dtype='float32')
                self.custom_audio_index[item['audiotype']] = 0
            self.custom_index[item['audiotype']] = 0
            # self.custom_opt[item['audiotype']] = item

    def init_customindex(self):
        self.custom_audiotype = 0
        for key in self.custom_audio_index:
            self.custom_audio_index[key] = 0
        for key in self.custom_index:
            self.custom_index[key] = 0

    def notify(self, eventpoint:dict | None):
        if eventpoint and eventpoint.get('status'):
            logger.info("notify:%s", eventpoint)
            # 首音延迟分解：把"回复开始 → 首段入 TTS → 首帧音频真正上屏"
            # 拆成两段打出来。这中间的差值就是 TTS 合成 + 口型渲染管线
            # 的开销，是"速度不够快"的定位依据（HANDOFF_FIXES.md P1）。
            if (
                not self._first_audio_logged
                and eventpoint.get('status') == 'start'
                and self._first_segment_at is not None
            ):
                self._first_audio_logged = True
                now = time.monotonic()
                started = self._reply_started_at
                logger.info(
                    "[Latency] 回复开始→首段入TTS %.2fs；首段入TTS→首帧上屏 %.2fs",
                    (
                        self._first_segment_at - started
                        if started is not None
                        else float('nan')
                    ),
                    now - self._first_segment_at,
                )

    def get_custom_audio_stream(self, audiotype):
        idx = self.custom_audio_index[audiotype]
        stream = self.custom_audio_cycle[audiotype][idx:idx+self.chunk]
        self.custom_audio_index[audiotype] += self.chunk
        if self.custom_audio_index[audiotype] >= self.custom_audio_cycle[audiotype].shape[0]:
            self.custom_audiotype = 1
        return stream
    
    def set_custom_state(self, audiotype, reinit=True):
        logger.info("set_custom_state: %s", audiotype)
        if audiotype == 0:
            self.custom_audiotype = 0
            return
        if self.custom_index.get(audiotype) is None:
            return
        self.custom_audiotype = audiotype
        if reinit:
            if audiotype in self.custom_audio_index:
                self.custom_audio_index[audiotype] = 0
            self.custom_index[audiotype] = 0

    # ========================== 核心渲染及 Pipeline 桥接 ==========================
    def get_avatar_length(self):
        if hasattr(self, 'frame_list_cycle'):
            return len(self.frame_list_cycle)
        return 1
        
    def inference(self, quit_event):
        length = self.get_avatar_length()
        index = 0
        count = 0
        counttime = 0
        last_speaking = False
        batch_count = 0

        # syncnet_T = 12  # 时间步
        # weight_dtype = torch.float16  # 数据类型
        # infernum = 0
        logger.info('start inference')
        while not quit_event.is_set():
            starttime = time.perf_counter()
            audiofeat_batch = []
            try:
                audiofeat_batch = self.asr.feat_queue.get(block=True, timeout=1)
            except queue.Empty:
                continue
                
            is_all_silence = True
            audio_frames: list[AudioFrameData] = []
            type_counts = {}
            for _ in range(self.batch_size * 2):
                audioframe:AudioFrameData = self.asr.output_queue.get()
                if audioframe.type == 0:
                    is_all_silence = False
                type_counts[audioframe.type] = type_counts.get(audioframe.type, 0) + 1
                audio_frames.append(audioframe)

            current_speaking = not is_all_silence
            in_action = hasattr(self, 'action_start') and hasattr(self, 'action_end') and self.custom_audiotype > 0
            batch_count += 1

            if batch_count % 250 == 0:
                logger.debug(
                    "inference: silence=%s types=%s action=%s state=%s",
                    is_all_silence,
                    type_counts,
                    in_action,
                    self.custom_audiotype,
                )
            if in_action:
                eff_len = self.action_end - self.action_start + 1
                eff_off = self.action_start
                prev_start = getattr(self, '_prev_action_start', None)
                was_in_action = getattr(self, '_was_in_action', False)
                # 显式 reset 标志（由 trigger_transition 设置）
                if getattr(self, '_reset_index', False):
                    index = 0
                elif not was_in_action or (prev_start is not None and eff_off != prev_start):
                    index = 0
                self._prev_action_start = eff_off
                self._was_in_action = True
                if hasattr(self, '_reset_index'):
                    self._reset_index = False
            else:
                self._was_in_action = False
                eff_len = length
                eff_off = 0

            if is_all_silence:
                if in_action:
                    # 动作/过渡/Idle 模式（无语音）：播放当前帧范围
                    in_transit = getattr(self, '_in_transition', False)
                    is_talk_phase = not in_transit and (
                        eff_off == getattr(self, 'talk_start', -1) or
                        eff_off == getattr(self, 'talk2_start', -1))
                    is_idle_phase = not in_transit and self.custom_audiotype in (1, 2, 3)

                    for i in range(self.batch_size):
                        if is_talk_phase:
                            idx = eff_off + (index % eff_len)
                        else:
                            # Keep every audio/video slot in the batch. At a
                            # state boundary the final source frame is held
                            # until the next batch activates the next state.
                            idx = eff_off + min(index, eff_len - 1)
                        self.res_frame_queue.put((None, audio_frames[i*2:i*2+2], idx))
                        index = index + 1
                else:
                    # 无 action_start 时的 fallback idle
                    idle_ranges = getattr(self, 'idle_ranges', None)
                    if idle_ranges:
                        import random
                        if not hasattr(self, '_idle_r_idx'): self._idle_r_idx = 0; self._idle_f_idx = 0
                        r = idle_ranges[self._idle_r_idx % len(idle_ranges)]
                        ilen = r[1] - r[0] + 1
                        triggered = False
                        for i in range(self.batch_size):
                            if triggered:
                                # 跳过：advance 已触发过渡，不再产生旧 idle 帧
                                continue
                            idx = r[0] + (self._idle_f_idx % ilen)
                            self.res_frame_queue.put((None, audio_frames[i*2:i*2+2], idx))
                            self._idle_f_idx += 1
                            if self._idle_f_idx >= ilen:
                                self._idle_f_idx = 0
                                advance = getattr(self, '_advance_idle', None)
                                if advance:
                                    advance()
                                    triggered = True
                                else:
                                    cur = self._idle_r_idx
                                    pool = [x for x in range(len(idle_ranges)) if x != cur]
                                    self._idle_r_idx = random.choice(pool) if pool else (cur + 1) % len(idle_ranges)
                                    logger.info(f"[Idle] fallback switch {cur} -> {self._idle_r_idx}")
                    else:
                        for i in range(self.batch_size):
                            idx = mirror_index(length, index)
                            self.res_frame_queue.put((None, audio_frames[i*2:i*2+2], idx))
                            index = index + 1
            else:
                if current_speaking and not last_speaking:
                    if not in_action and self.custom_index.get(1) is not None:
                        index = 0  # only reset in normal (non-action) mode
                t = time.perf_counter()
                pred = self.inference_batch(index, audiofeat_batch)
                counttime += (time.perf_counter() - t)
                count += self.batch_size
                if count >= 100:
                    logger.info(f"------actual avg infer fps:{count/counttime:.4f}")
                    count = 0
                    counttime = 0
                for i, res_frame in enumerate(pred):
                    if in_action:
                        is_talk = (eff_off == getattr(self, 'talk_start', -1) or
                                   eff_off == getattr(self, 'talk2_start', -1))
                        if is_talk:
                            idx = eff_off + (index % eff_len)
                        else:
                            idx = eff_off + min(index, eff_len - 1)
                    else:
                        idx = mirror_index(length, index)
                    self.res_frame_queue.put((res_frame, audio_frames[i*2:i*2+2], idx))
                    index = index + 1

            if in_action and index >= eff_len:
                # ── 防止竞态条件：主线程可能在批次中途修改了 action_start ──
                if self.action_start != eff_off:
                    index = 0
                    continue

                cycle_complete = getattr(self, '_on_state_cycle_complete', None)
                if cycle_complete is not None:
                    cycle_complete(current_speaking)
                else:
                    # Backward-compatible behaviour for avatars without the
                    # choreography state machine.
                    index %= max(eff_len, 1)
                    continue
                index = 0

            if current_speaking != last_speaking:
                logger.info(f"inference 状态切换：{'说话' if last_speaking else '静音'} → {'说话' if current_speaking else '静音'}")
                if current_speaking:
                    self._speech_ever_active = True
                    self._speech_started = True
                    # 语音从 idle 开始时立即过渡到 talk（排队动作优先时不触发）
                    go_talk = getattr(self, '_go_talk_if_speaking', None)
                    if go_talk is not None:
                        go_talk()
                last_speaking = current_speaking
        logger.info('baseavatar inference thread stop')

    def _draw_thinking(self, frame, t):
        """黑屏 + 三个脉冲光点动画（类似 iMessage typing）"""
        import math
        h, w = frame.shape[:2]
        frame[:] = (10, 10, 15)  # 深色背景，非纯黑
        cx, cy = w // 2, int(h * 0.72)  # 底部居中
        spacing = int(w * 0.06)
        for i in range(3):
            # 相位差 120°，周期 ~1.5s
            phase = t / 15.0 * math.pi * 2 + i * math.pi * 2 / 3
            r = int(w * 0.012 * (0.4 + 0.6 * (math.sin(phase) * 0.5 + 0.5)))
            x = cx + (i - 1) * spacing
            # 光晕
            for ring in range(3, 0, -1):
                rr = r + ring * 2
                alpha = 0.08 / ring
                cv2.circle(frame, (x, cy), rr, (60, 60, 80), -1)
                cv2.circle(frame, (x, cy), rr, (80, 80, 100), 1)
            # 核心光点
            cv2.circle(frame, (x, cy), r, (180, 180, 200), -1)
            cv2.circle(frame, (x, cy), max(r-1, 1), (220, 220, 240), -1)
        return frame

    def process_frames(self,quit_event):
        self.output.start()

        while not quit_event.is_set():
            try:
                audio_frames: list[AudioFrameData]
                res_frame,audio_frames,idx = self.res_frame_queue.get(block=True, timeout=1)
            except queue.Empty:
                continue

            is_speech_frame = not (audio_frames[0].type!=0 and audio_frames[1].type!=0)

            # ── 帧处理 ──
            if getattr(self, 'thinking', False):
                if is_speech_frame:
                    self.thinking = False
                if getattr(self, '_think_frame', None) is None:
                    self._think_frame = np.zeros_like(self.frame_list_cycle[0])
                    self._think_t = 0
                self._think_t += 1
                combine_frame = self._draw_thinking(self._think_frame.copy(), self._think_t)
                self.speaking = is_speech_frame
            elif is_speech_frame:
                self.speaking = True
                try:
                    combine_frame = self.paste_back_frame(res_frame, idx)
                except Exception as e:
                    logger.warning(f"paste_back_frame error: {e}")
                    continue
            else:
                self.speaking = False
                combine_frame = self.frame_list_cycle[idx]

            self.output.push_video_frame(combine_frame)

            for audio_frame in audio_frames:
                frame_pcm = (audio_frame.data * 32767).astype(np.int16)
                self.output.push_audio_frame(frame_pcm, audio_frame.userdata)

        self.output.stop()
        logger.info('baseavatar process_frames thread stop')

    def _consume_transition_frame(self):
        """Get next frame from active transition, or None if no transition.

        Driven by the choreography state machine (state_machine.py).
        Returns (frame, is_first, is_last) tuple.
        """
        pending = getattr(self, '_pending_transition', None)
        if pending is None:
            return None, False, False
        idx = self._transition_idx
        if idx >= len(pending):
            self._last_transition_frame = pending[-1] if pending else None
            self._pending_transition = None
            self._transition_idx = 0
            self._blend_from_transition = 3  # frames to crossfade after transition
            return None, False, True
        frame = pending[idx]
        is_first = (idx == 0)
        self._transition_idx += 1
        return frame, is_first, False

    def render(self,quit_event):
        self.quit_event = quit_event
        
        self.init_customindex()
        self._idle_cycle_count = 0
        self.tts.render(quit_event)

        infer_quit_event = mp.Event()
        infer_thread = Thread(target=self.inference, args=(infer_quit_event,))
        infer_thread.start()
        
        process_quit_event = Event()
        process_thread = Thread(target=self.process_frames, args=(process_quit_event,))
        process_thread.start()

        while not quit_event.is_set(): 
            self.asr.run_step()
        logger.info('baseavatar render thread stop')

        infer_quit_event.set()
        infer_thread.join()

        process_quit_event.set()
        process_thread.join()
