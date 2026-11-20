"""采集窗口快照契约：按需解码、时序切片与快照隔离。

阶段四验收证据：内存窗口不整体物化帧、慢消费者持有快照不受后续采集
影响、设备代际替换不混入旧任务证据。共享视频 pass 的语义由测量域
test_shared_rppg_analysis.py 覆盖。
"""
import time

import numpy as np
import pytest

from server.capture.domain import AudioWindow
from server.capture.webrtc.audio import AudioBuffer
from server.capture.webrtc.kernel import CaptureKernel
from server.tests.contract_stubs import build_jpeg_bytes


@pytest.fixture
def video_source():
    now = [0.0]
    kernel = CaptureKernel(clock=lambda: now[0])

    def seed(count, start=12.0, size=(64, 48), step=0.1):
        jpeg = build_jpeg_bytes(size=size)
        for index in range(count):
            now[0] = start + index * step
            kernel.publish_frame(jpeg, now[0], now[0])

    return kernel, seed


def _count(window):
    """窗口帧数以流式迭代计数（VideoWindow 不再提供 len）。"""
    return sum(1 for _ in window)


def test_video_window_expires_frames_by_arrival_time_not_count(video_source):
    """帧日志按 60s 到达时间滑动淘汰；帧数不再是容量单位。"""
    kernel, seed = video_source
    seed(2000, start=0.0)
    # 2000 帧 × 0.1s = 200s 跨度：60s 时间窗只剩最后 601 帧
    assert _count(kernel.video_window()) == 601


def test_video_window_keeps_dense_streams_beyond_any_count_budget(video_source):
    kernel, seed = video_source
    seed(2000, start=0.0, step=0.01)
    # 20s 内的 2000 帧全部保留（旧帧数上限会截断到 1500）
    assert _count(kernel.video_window()) == 2000


def test_video_window_slices_the_last_seconds_on_request(video_source):
    """时序切片：要几秒给最后几秒，右端对齐最新到达帧。"""
    kernel, seed = video_source
    seed(100, start=0.0)  # 0.0s..9.9s，0.1s 步进
    assert _count(kernel.video_window(3.0)) == 31  # 6.9s（含）之后的帧
    assert _count(kernel.video_window(0.05)) == 1
    assert kernel.video_window(1000.0) is not None


def test_video_window_is_a_snapshot_unaffected_by_later_publication(video_source):
    kernel, seed = video_source
    seed(700, start=0.0, step=0.01)
    window = kernel.video_window()
    # 快照只复制日志引用；后续发布不影响已取得的窗口
    seed(1, start=1000.0)
    assert _count(window) == 700
    # 新到达帧把窗口外旧证据淘汰：新窗口只剩最新一帧
    assert _count(kernel.video_window()) == 1


def test_window_frames_decode_on_demand_and_survive_generation_replacement(video_source):
    kernel, seed = video_source
    seed(4)
    window = kernel.video_window()

    iterator = iter(window)
    first = next(iterator)

    # 消费者在迭代中途遇到代际替换：快照字节独立，剩余帧仍按原序解码
    kernel.clear_media_state()
    seed(2, start=99.0, size=(64, 48))
    rest = list(iterator)
    assert len(rest) == 3
    assert first.data.shape == rest[0].data.shape
    # 新代际窗口只包含替换后的证据；旧窗口不受污染
    new_window = kernel.video_window()
    assert _count(new_window) == 2
    np.testing.assert_array_equal(first.data, next(iter(window)).data)


def test_window_isolates_slow_consumers_without_backpressure(video_source):
    kernel, seed = video_source
    seed(3)
    window = kernel.video_window()
    frames = list(window)
    # 慢消费者逐帧处理期间，采集持续推进；窗口数据仍完整可用
    seed(5, start=50.0)
    assert len(frames) == 3
    assert _count(window) == 3


def test_empty_kernel_has_no_windows():
    kernel = CaptureKernel()
    assert kernel.video_window() is None
    assert kernel.audio_window() is None
    assert kernel.audio_window(sample_rate=16000) is None
    assert kernel.heart_rate_window() is None
    assert kernel.temperature_window() is None


def test_audio_window_resamples_to_requested_rate_and_reports_arrival():
    kernel = CaptureKernel()
    pcm_48k = b"\x01\x00" * 48000  # 1 秒单声道 PCM16
    kernel.audio_buffer.extend(pcm_48k)

    native = kernel.audio_window()
    assert isinstance(native, AudioWindow)
    assert (native.sample_rate, native.pcm) == (48000, pcm_48k)
    assert native.received_at is not None  # 窗口右端对齐最近一次到达

    sliced = kernel.audio_window(0.5)
    assert len(sliced.pcm) == 48000  # 最后 0.5 秒 = 24000 样本

    asr = kernel.audio_window(sample_rate=16000)
    assert asr.sample_rate == 16000
    # 重采样后样本数≈1 秒（48000→16000），字节数=样本数×2
    assert abs(len(asr.pcm) // 2 - 16000) <= 8


def test_audio_window_clears_to_none_after_generation_replacement():
    kernel = CaptureKernel()
    kernel.audio_buffer.extend(b"\x01\x00" * 480)
    assert kernel.audio_window() is not None
    kernel.clear_media_state()
    assert kernel.audio_window() is None


def test_audio_buffer_arrival_tracking_survives_partial_refill():
    """清空后再次到达音频：到达时刻随新数据刷新。"""
    import time as _time

    buffer = AudioBuffer()
    now = _time.monotonic()
    buffer.extend(b"\x00\x00" * 480, clock=lambda: now - 5.0)
    assert buffer.last_arrival == now - 5.0

    buffer.clear()
    assert buffer.last_arrival is None

    buffer.extend(b"\x00\x00" * 480, clock=lambda: now - 1.0)
    assert buffer.last_arrival == now - 1.0
    assert buffer.duration_seconds == 480 * 2 / 96000


@pytest.mark.parametrize("age, available", [(1.999, True), (2.0, False), (3600.0, False)])
def test_live_window_freshness_is_independent_of_tail_length(age, available):
    now = [100.0]
    kernel = CaptureKernel(clock=lambda: now[0])
    kernel.publish_frame(build_jpeg_bytes(), now[0], now[0])
    snapshot = kernel.video_window()
    now[0] += age
    for seconds in (None, 0.05, 60.0, 1000.0):
        assert (kernel.video_window(seconds) is not None) is available
    # Expiry affects future live reads, never snapshots already handed out.
    assert _count(snapshot) == 1
    kernel.publish_frame(build_jpeg_bytes(), now[0], now[0])
    assert kernel.video_window() is not None
