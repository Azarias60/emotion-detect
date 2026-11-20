"""采集层物理与时间线基线：帧物化等价、方向归一与 PCM 容量。

计算域的 live 观察基线已随三件套收口迁入各域测试文件：疲劳侧见
test_fatigue_video_coordination.py，表情侧见 test_expression_video_coordination.py，
测量侧见 test_rppg_face_mesh_result_boundary.py。
"""

import cv2
import numpy as np
import pytest

from server.capture.webrtc.audio import AudioBuffer
from server.capture.webrtc.jpeg import OpenCvCanonicalFrameDecoder
from server.capture.webrtc.kernel import CaptureKernel
from server.tests.contract_stubs import build_jpeg_bytes


@pytest.mark.parametrize("times", [(12.0,), (12.0, 12.25, 13.0), (12.0, 12.001)])
def test_capture_window_preserves_device_timestamps_and_decoded_bgr(times):
    kernel = CaptureKernel(clock=lambda: times[-1])
    jpeg = build_jpeg_bytes(size=(32, 24))
    raw_pixels = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    expected_pixels = np.fliplr(np.rot90(raw_pixels, k=1)).copy()
    for timestamp in times:
        kernel.publish_frame(jpeg, timestamp, timestamp)

    window = kernel.video_window()
    assert window is not None
    assert [frame.timestamp for frame in window] == list(times)
    # 独立迭代器各自从头解码，帧序、尺寸与像素与旧物化链路一致
    first = [frame.data.copy() for frame in window]
    second = [frame.data.copy() for frame in window]
    assert len(first) == len(second) == len(times)
    for frame in first + second:
        np.testing.assert_array_equal(frame, expected_pixels)


def test_capture_window_frames_match_the_retired_mp4_relay_within_codec_tolerance(
    tmp_path,
):
    """旧 MP4 物化（mp4v 重编码）与新内存帧的差异只来自该次重编码。"""
    timestamps = tuple(12.0 + index * 0.1 for index in range(8))
    kernel = CaptureKernel(clock=lambda: timestamps[-1])
    jpeg = build_jpeg_bytes(size=(64, 48))
    raw_pixels = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    expected_pixels = np.fliplr(np.rot90(raw_pixels, k=1)).copy()
    for timestamp in timestamps:
        kernel.publish_frame(jpeg, timestamp, timestamp)

    window = kernel.video_window()
    memory_frames = [frame.data for frame in window]

    # 以同一公式与编码参数重建旧链路的 MP4（mp4v），对比解码结果
    relay_path = str(tmp_path / "mcu_video.mp4")
    height, width = memory_frames[0].shape[:2]  # 规范方向为竖屏（旋转 90°）
    writer = cv2.VideoWriter(
        relay_path, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (width, height),
    )
    assert writer.isOpened()
    try:
        for frame in memory_frames:
            writer.write(frame)
    finally:
        writer.release()
    capture = cv2.VideoCapture(relay_path)
    relay_frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        relay_frames.append(frame)
    capture.release()

    assert len(relay_frames) == len(memory_frames)
    for memory, relay in zip(memory_frames, relay_frames):
        assert memory.shape == relay.shape
        difference = np.abs(memory.astype(np.int16) - relay.astype(np.int16))
        assert difference.mean() <= 4.0  # 仅 mp4v 有损重编码差异
    np.testing.assert_array_equal(memory_frames[0], expected_pixels)


def test_capture_pcm_sixty_second_capacity_at_48k():
    buffer = AudioBuffer()
    buffer.extend(b"\x02\x00")
    buffer.extend(b"\x01\x00" * (48000 * 60))
    assert len(buffer) == 5_760_000
    assert buffer.snapshot() == b"\x01\x00" * (48000 * 60)
