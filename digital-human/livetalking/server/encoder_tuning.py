# -*- coding: utf-8 -*-
"""H264 编码器调优：把 aiortc 的 Baseline profile 换成 High。

为什么要打这个补丁（2026-11-13 用户反馈"画面模糊/发虚"）：
aiortc 1.15 的 ``H264Encoder._encode_frame`` 里硬编码了
``profile="Baseline"`` + ``tune=zerolatency``（视频会议取向）。
Baseline 只能用 CAVLC 熵编码、不支持 8x8 变换，同码率下压缩效率比 High
低 20~30%——704×1280 这种细节密集的竖屏人像（头发、皮肤纹理、衣服褶皱）
糊得最明显。链路其它环节都查过没有降采样（源帧 704×1280、码率 6Mbps、
前端按高度等比缩放），所以画质损失就出在这个 profile 上。

Chrome / Edge / Safari 全都支持 High profile（现代 WebRTC 默认就是 High），
换掉不影响兼容性。保留 zerolatency（低延迟必需）与 level 40
（704×1280@30fps 的宏块率接近 level 3.1 上限，放宽到 4.0 避免码率被压）。

用法：``import server.encoder_tuning``（放 app.py 顶部，早于任何
RTCPeerConnection 创建即可）。
"""
from __future__ import annotations

import fractions

import av
from aiortc.codecs import h264

_ORIGINAL_ENCODE_FRAME = h264.H264Encoder._encode_frame
_patched = False


def _encode_frame_high_profile(self, frame, force_keyframe):
    """与 aiortc 原实现逐行一致，只改 profile / level / preset。"""
    if self.codec and (
        frame.width != self.codec.width
        or frame.height != self.codec.height
        or abs(self.target_bitrate - self.codec.bit_rate) / self.codec.bit_rate > 0.1
    ):
        self.buffer_data = b""
        self.buffer_pts = None
        self.codec = None

    if force_keyframe:
        frame.pict_type = av.video.frame.PictureType.I
    else:
        frame.pict_type = av.video.frame.PictureType.NONE

    if self.codec is None:
        self.codec = av.CodecContext.create("libx264", "w")
        self.codec.width = frame.width
        self.codec.height = frame.height
        self.codec.bit_rate = self.target_bitrate
        self.codec.pix_fmt = "yuv420p"
        self.codec.framerate = fractions.Fraction(h264.MAX_FRAME_RATE, 1)
        self.codec.time_base = fractions.Fraction(1, h264.MAX_FRAME_RATE)
        self.codec.options = {
            "level": "40",
            "tune": "zerolatency",
            "preset": "veryfast",
        }
        # ← 唯一实质改动：Baseline → High
        self.codec.profile = "High"

    data_to_send = b""
    for package in self.codec.encode(frame):
        data_to_send += bytes(package)
    if data_to_send:
        yield from self._split_bitstream(data_to_send)


def apply() -> None:
    global _patched
    if _patched:
        return
    h264.H264Encoder._encode_frame = _encode_frame_high_profile
    _patched = True


apply()
