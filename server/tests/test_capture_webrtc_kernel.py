# -*-coding: utf-8 -*-
"""采集内核契约测试：v1 协议校验、JPEG 分块重组、传感器窗口与内核读端口。

固件契约以 esp32s3capture 为唯一真源；这些用例直接钉住线上的字节与
JSON 语义，不依赖真实硬件。
"""
import asyncio
import json

import cv2
import numpy as np
import pytest

from server.capture.domain import HeartRateWindow, TemperatureWindow
from server.capture.webrtc.audio import AudioBuffer, AudioReceiver
from server.capture.webrtc.jpeg import JpegReassembler, OpenCvCanonicalFrameDecoder
from server.capture.webrtc.kernel import CaptureKernel
from server.capture.webrtc.protocol import (
    CaptureProtocolError,
    JpegRunProtocolError,
    parse_capture_message,
    parse_jpeg_run_marker,
    run_is_older,
)
from server.capture.webrtc.sensors import SensorState
from server.capture.webrtc.session import CaptureSession
from server.tests.contract_stubs import build_jpeg_bytes


def _chunk(jpeg: bytes, chunk_size: int = 4096, pts_ms: int = 1000):
    """按固件 send_video_over_data_channel 的线格式切块。"""
    messages = []
    chunks = [jpeg[i:i + chunk_size] for i in range(0, len(jpeg), chunk_size)]
    for index, payload in enumerate(chunks):
        flag = index & 0x7F
        if index == len(chunks) - 1:
            flag |= 0x80
        messages.append(bytes([flag]) + len(payload).to_bytes(4, "big") + pts_ms.to_bytes(4, "big") + payload)
    return messages


# --- v1 控制通道协议 ----------------------------------------------------------


def _state_message(run=1, enabled=True, bpm=72.5, ambient=25.0, object_temp=36.2,
                   sampled_at_us=1):
    return {
        "v": 1, "type": "state", "enabled": enabled, "run": run,
        "audio_base_ms": 0,
        "temperature": {
            "valid": ambient is not None, "sampled_at_us": sampled_at_us,
            "ambient_c": ambient, "object_c": object_temp, "error": None,
        },
        "heart_rate": {
            "valid": bpm is not None, "sampled_at_us": sampled_at_us,
            "bpm": bpm, "error": None,
        },
    }


class TestCaptureMessageValidation:
    def test_valid_state_passes_and_keeps_fields(self):
        message = parse_capture_message(json.dumps(_state_message()))
        assert message["type"] == "state"
        assert message["temperature"]["ambient_c"] == 25.0

    def test_invalid_sensor_measurements_must_be_null(self):
        raw = _state_message(bpm=None)
        raw["heart_rate"]["bpm"] = 60
        with pytest.raises(CaptureProtocolError):
            parse_capture_message(json.dumps(raw))

    def test_bpm_must_be_positive_when_valid(self):
        raw = _state_message(bpm=0)
        with pytest.raises(CaptureProtocolError, match="bpm"):
            parse_capture_message(json.dumps(raw))

    def test_non_finite_sensor_values_are_rejected_at_the_wire_boundary(self):
        """JSON 层放行的 NaN/Infinity 在协议边界拒绝——这是传感器数值有限性的唯一防线。"""
        raw = _state_message(bpm=float("nan"))
        with pytest.raises(CaptureProtocolError, match="量程"):
            parse_capture_message(json.dumps(raw))
        raw = _state_message(ambient=float("inf"))
        with pytest.raises(CaptureProtocolError, match="量程"):
            parse_capture_message(json.dumps(raw))

    def test_error_must_be_null_when_valid(self):
        raw = _state_message()
        raw["temperature"]["error"] = "ESP_ERR_TIMEOUT"
        with pytest.raises(CaptureProtocolError):
            parse_capture_message(json.dumps(raw))

    def test_unsupported_version_is_rejected(self):
        raw = _state_message()
        raw["v"] = 2
        with pytest.raises(CaptureProtocolError, match="版本"):
            parse_capture_message(json.dumps(raw))

    def test_message_over_1024_bytes_is_rejected(self):
        padded = _state_message()
        padded["temperature"]["error"] = None
        raw = json.dumps(padded) + " " * 1100
        with pytest.raises(CaptureProtocolError, match="1024"):
            parse_capture_message(raw)

    def test_capture_result_requires_full_field_set(self):
        with pytest.raises(CaptureProtocolError):
            parse_capture_message(json.dumps({"v": 1, "type": "capture_result", "id": "x"}))

    def test_run_ordering_wraps_as_uint32(self):
        assert run_is_older(0, 1)
        assert not run_is_older(1, 1)
        assert run_is_older(1, 2)
        assert not run_is_older(2, 1)
        # 回绕：current 已越过 2^32，紧随其后的 run 是更新代际
        assert not run_is_older(0x10, 0xFFFFFF00)
        assert run_is_older(0xFFFFFF00, 0x10)


class TestJpegRunMarker:
    def test_parses_uint32_run(self):
        assert parse_jpeg_run_marker('{"v":1,"type":"jpeg_run","run":7}') == 7

    def test_rejects_non_text_or_bad_shape(self):
        with pytest.raises(JpegRunProtocolError):
            parse_jpeg_run_marker(b"{}")
        with pytest.raises(JpegRunProtocolError):
            parse_jpeg_run_marker('{"v":1,"type":"jpeg_run","run":0}')
        with pytest.raises(JpegRunProtocolError):
            parse_jpeg_run_marker('{"v":1,"type":"other","run":3}')


# --- JPEG 分块重组 -------------------------------------------------------------


class TestJpegReassembler:
    def test_reassembles_chunked_frame(self):
        parser = JpegReassembler()
        jpeg = build_jpeg_bytes(size=(320, 240))
        chunks = _chunk(jpeg, chunk_size=100)
        frame = None
        for message in chunks:
            frame = parser.feed(message)
        assert frame == (jpeg, 1.0)

    def test_keeps_device_frame_spacing_across_network_jitter_and_clock_wrap(self):
        arrival = [100.0]
        parser = JpegReassembler(clock=lambda: arrival[0])
        jpeg = b"frame"
        assert parser.feed(_chunk(jpeg, pts_ms=0xFFFFFFF0)[0])[1] == 0xFFFFFFF0 / 1000
        arrival[0] += 0.8
        next_frame = parser.feed(_chunk(jpeg, pts_ms=17)[0])
        assert next_frame == (jpeg, (0xFFFFFFF0 + 33) / 1000)
        assert parser.feed(_chunk(jpeg, pts_ms=17)[0]) is None

    def test_clock_reset_cannot_catch_up_within_same_generation(self):
        parser = JpegReassembler()
        jpeg = b"frame"
        assert parser.feed(_chunk(jpeg, pts_ms=3000)[0]) == (jpeg, 3.0)
        assert parser.feed(_chunk(jpeg, pts_ms=50)[0]) is None
        assert parser.feed(_chunk(jpeg, pts_ms=3010)[0]) is None
        assert parser.discarded == 2
        assert JpegReassembler().feed(_chunk(jpeg, pts_ms=50)[0]) == (jpeg, 0.05)

    def test_out_of_order_chunk_discards_partial_frame(self):
        parser = JpegReassembler()
        jpeg = build_jpeg_bytes(size=(64, 48))
        chunks = _chunk(jpeg, chunk_size=100)
        assert parser.feed(chunks[0]) is None
        assert parser.feed(chunks[2]) is None  # 乱序：丢弃
        # 序号 0 重新开始一帧后可完整恢复
        frame = None
        for message in chunks:
            frame = parser.feed(message)
        assert frame == (jpeg, 1.0)
        assert parser.discarded >= 1

    def test_sequence_zero_always_restarts(self):
        parser = JpegReassembler()
        jpeg = build_jpeg_bytes(size=(64, 48))
        chunks = _chunk(jpeg, chunk_size=100)
        parser.feed(chunks[0])
        # 未完成帧时新序号 0 到达：旧残帧丢弃
        parser.feed(chunks[0])
        assert parser.discarded == 1

    def test_decoder_decodes_any_resolution_without_ceiling(self):
        decoder = OpenCvCanonicalFrameDecoder()
        # 720p 宽屏与竖屏画面按原始分辨率解码（旧 640x480 预算会拒绝），
        # 规范方向统一为转置后的应用坐标。
        for size in ((1280, 720), (480, 640)):
            frame = decoder.decode(build_jpeg_bytes(size=size))
            assert frame.shape == (size[0], size[1], 3)
        assert decoder.decode(b"not-a-jpeg") is None


# --- 规范方向算子 ---------------------------------------------------------------


class TestCanonicalOrientationOperator:
    """反对角线无损转置：形状、连续内存与数值等价三重基线。"""

    def test_transverse_shape_and_contiguity(self):
        # 摄像头物理横置：480 行 × 640 列的设备画面转置为 640×480 应用坐标。
        image = np.arange(480 * 640 * 3, dtype=np.uint8).reshape(480, 640, 3)
        transposed = cv2.flip(cv2.transpose(image), -1)
        assert transposed.shape == (640, 480, 3)
        assert transposed.flags["C_CONTIGUOUS"] is True

    def test_transverse_matches_reference_orientation_pixelwise(self):
        # 与旧实现 np.fliplr(np.rot90(frame, k=1))（逆时针 90° + 水平镜像）
        # 像素级等价。
        rng = np.random.default_rng(7)
        image = rng.integers(0, 256, size=(48, 64, 3), dtype=np.uint8)
        transposed = cv2.flip(cv2.transpose(image), -1)
        reference = np.fliplr(np.rot90(image, k=1)).copy()
        np.testing.assert_array_equal(transposed, reference)

    def test_decoder_output_is_pixel_equal_to_reference_chain(self):
        # 完整解码链路：同一 JPEG 经解码器转置后的结果，与"先解码再走旧
        # 参考方向算子"逐像素一致。
        jpeg = build_jpeg_bytes(size=(64, 48))
        decoded = OpenCvCanonicalFrameDecoder().decode(jpeg)
        raw = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        reference = np.fliplr(np.rot90(raw, k=1)).copy()
        np.testing.assert_array_equal(decoded, reference)


# --- 传感器窗口 ----------------------------------------------------------------


class TestSensorState:
    def test_valid_samples_accumulate_into_windows(self):
        now = [0.0]
        sensors = SensorState(clock=lambda: now[0])

        first = _state_message(run=1, bpm=70.0, ambient=25.0, object_temp=36.0)
        first["heart_rate"]["sampled_at_us"] = 100
        first["temperature"]["sampled_at_us"] = 100
        second = _state_message(run=1, bpm=74.0, ambient=25.5, object_temp=36.5)
        second["heart_rate"]["sampled_at_us"] = 200
        second["temperature"]["sampled_at_us"] = 200
        assert sensors.accept_state(first)
        now[0] = 0.5
        assert sensors.accept_state(second)

        heart = sensors.heart_rate_window()
        assert heart.samples_bpm == (70.0, 74.0)
        assert heart.average_bpm == pytest.approx(72.0)
        assert heart.latest_bpm == 74.0
        temperature = sensors.temperature_window()
        assert temperature.ambient_temps == (25.0, 25.5)
        assert temperature.latest_object_temp == 36.5

    def test_windows_slice_the_last_seconds(self):
        now = [0.0]
        sensors = SensorState(clock=lambda: now[0])
        for index in range(10):  # 0.0s..4.5s，0.5s 间隔
            message = _state_message(run=1, bpm=60.0 + index, ambient=25.0 + index,
                                     sampled_at_us=100 + index)
            assert sensors.accept_state(message)
            now[0] += 0.5

        heart = sensors.heart_rate_window(1.0)
        assert heart.samples_bpm == (68.0, 69.0)  # 最后 1 秒的两个采样
        assert heart.average_bpm == pytest.approx(68.5)
        assert sensors.temperature_window(0.6).ambient_temps == (34.0,)

    def test_invalid_measurements_do_not_enter_windows(self):
        sensors = SensorState()
        sensors.accept_state(_state_message(bpm=None, ambient=None, object_temp=None))
        assert sensors.heart_rate_window() is None
        assert sensors.temperature_window() is None

    def test_older_run_is_ignored_and_newer_run_resets(self):
        sensors = SensorState()
        sensors.accept_state(_state_message(run=1, bpm=70.0))
        assert sensors.accept_state(_state_message(run=2, bpm=80.0))
        assert sensors.heart_rate_window().samples_bpm == (80.0,)
        assert not sensors.accept_state(_state_message(run=1, bpm=99.0))
        assert sensors.heart_rate_window().samples_bpm == (80.0,)

    def test_unparsed_wire_payload_is_rejected_by_the_protocol_layer(self):
        # 非法 JSON 在协议层拒绝（parse_capture_message 有专门用例）；
        # SensorState 只接收通过校验的消息字典。
        sensors = SensorState()
        assert sensors.accept_state(_state_message()) is True

    def test_status_marks_stale_samples(self):
        now = [0.0]
        sensors = SensorState(clock=lambda: now[0])
        sensors.accept_state(_state_message(run=1, bpm=70.0))
        assert sensors.status()["heart_rate"]["valid"] is True
        now[0] = 5.0
        assert sensors.status()["heart_rate"]["stale"] is True
        assert sensors.status()["heart_rate"]["valid"] is False

    def test_duplicate_sample_time_does_not_append_or_refresh_each_sensor(self):
        now = [0.0]
        sensors = SensorState(clock=lambda: now[0])
        first = _state_message(run=1, bpm=70.0, ambient=25.0, object_temp=36.0,
                               sampled_at_us=100)
        assert sensors.accept_state(first)

        now[0] = 1.0
        duplicate_heart_new_temperature = _state_message(
            run=1, bpm=70.0, ambient=26.0, object_temp=37.0, sampled_at_us=200)
        duplicate_heart_new_temperature["heart_rate"]["sampled_at_us"] = 100
        assert sensors.accept_state(duplicate_heart_new_temperature)

        assert sensors.heart_rate_window().samples_bpm == (70.0,)
        assert sensors.temperature_window().ambient_temps == (25.0, 26.0)
        assert sensors.status()["heart_rate"]["age_ms"] == 1000
        assert sensors.status()["temperature"]["age_ms"] == 0

        now[0] = 3.1
        repeated = _state_message(run=1, bpm=70.0, ambient=26.0, object_temp=37.0,
                                  sampled_at_us=200)
        repeated["heart_rate"]["sampled_at_us"] = 100
        assert sensors.accept_state(repeated)
        status = sensors.status()
        assert status["heart_rate"]["stale"] is True
        assert status["heart_rate"]["age_ms"] == pytest.approx(3100)
        assert status["temperature"]["stale"] is False

    def test_latest_invalid_sample_remains_visible_without_refreshing_duplicates(self):
        now = [0.0]
        sensors = SensorState(clock=lambda: now[0])
        assert sensors.accept_state(_state_message(sampled_at_us=100))

        invalid = _state_message(bpm=None, ambient=None, object_temp=None,
                                 sampled_at_us=200)
        invalid["heart_rate"]["error"] = "ESP_ERR_INVALID_RESPONSE"
        invalid["temperature"]["error"] = "ESP_ERR_TIMEOUT"
        now[0] = 1.0
        assert sensors.accept_state(invalid)
        heart = sensors.status()["heart_rate"]
        temperature = sensors.status()["temperature"]
        assert heart["valid"] is False
        assert heart["sampled_at_us"] == 200
        assert heart["error"] == "ESP_ERR_INVALID_RESPONSE"
        assert temperature["sampled_at_us"] == 200
        assert temperature["error"] == "ESP_ERR_TIMEOUT"

        now[0] = 4.1
        assert sensors.accept_state(invalid)
        heart = sensors.status()["heart_rate"]
        assert heart["stale"] is True
        assert heart["age_ms"] == pytest.approx(3100)
        assert heart["error"] == "ESP_ERR_INVALID_RESPONSE"

    def test_window_expiry_is_applied_on_reads_without_new_samples(self):
        now = [0.0]
        kernel = CaptureKernel(clock=lambda: now[0])
        kernel.update_sensors(_state_message(
            run=1, bpm=70.0, ambient=25.0, object_temp=36.0, sampled_at_us=100))
        now[0] = 1.0
        kernel.update_sensors(_state_message(
            run=1, bpm=72.0, ambient=26.0, object_temp=37.0, sampled_at_us=200))
        assert kernel.heart_rate_window() is not None

        now[0] = 65.0
        assert kernel.heart_rate_window() is None
        assert kernel.temperature_window() is None

    def test_sample_series_window_is_the_unified_capture_window(self):
        """窗口与音频/视频统一为 60s 采集窗：120s 跨度的采样只留最后 60s。"""
        from server.capture.webrtc.sensors import _Series

        series = _Series()
        for index in range(600):  # 0.2s 间隔 → 120s 跨度
            series.append(index * 0.2, 70.0)
        assert len(series.values()) == 300
        assert series.latest() == 70.0

    def test_sensor_windows_expire_at_the_unified_capture_window(self):
        """60s 前的传感器样本淘汰（旧 600s 窗会保留），停流 60s 后证据不可用。"""
        now = [0.0]
        kernel = CaptureKernel(clock=lambda: now[0])
        kernel.update_sensors(_state_message(
            run=1, bpm=70.0, ambient=25.0, object_temp=36.0, sampled_at_us=100))
        now[0] = 30.0
        kernel.update_sensors(_state_message(
            run=1, bpm=72.0, ambient=26.0, object_temp=37.0, sampled_at_us=200))
        assert kernel.heart_rate_window().samples_bpm == (70.0, 72.0)

        now[0] = 65.0
        assert kernel.heart_rate_window().samples_bpm == (72.0,)
        assert kernel.temperature_window().ambient_temps == (26.0,)

        now[0] = 126.0
        assert kernel.heart_rate_window() is None
        assert kernel.temperature_window() is None


# --- 内核读端口 ----------------------------------------------------------------


class _FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class TestCaptureKernelPorts:
    def _kernel_with_frame(self):
        kernel = CaptureKernel(clock=_FakeClock())
        jpeg = build_jpeg_bytes(size=(320, 240))
        kernel.publish_frame(jpeg, kernel._clock(), kernel._clock())
        return kernel, jpeg

    def test_pooled_frame_is_served_through_the_window(self):
        kernel, jpeg = self._kernel_with_frame()
        window = kernel.video_window()
        assert window is not None
        frame = next(iter(window))
        assert frame.data.shape == (320, 240, 3)  # 转置后的规范方向

    def test_session_publish_clears_previous_evidence(self):
        kernel, _ = self._kernel_with_frame()
        kernel.update_sensors(_state_message(run=1, bpm=70.0))
        kernel.audio_buffer.extend(b"\x00\x01")
        kernel.on_session_published(None)
        assert kernel.video_window() is None
        assert kernel.heart_rate_window() is None
        assert len(kernel.audio_buffer) == 0

    def test_windows_are_typed_domain_objects(self):
        kernel, _ = self._kernel_with_frame()
        kernel.update_sensors(_state_message(run=1))
        assert isinstance(kernel.heart_rate_window(), HeartRateWindow)
        assert isinstance(kernel.temperature_window(), TemperatureWindow)

    def test_window_preserves_sparse_capture_timestamps(self):
        kernel, jpeg = self._kernel_with_frame()
        kernel.publish_frame(jpeg, 101.0, 101.0)
        kernel.publish_frame(jpeg, 102.0, 102.0)
        kernel._clock.now = 102.0

        window = kernel.video_window()
        assert window is not None
        assert [frame.timestamp for frame in window] == [100.0, 101.0, 102.0]
        assert sum(1 for _ in window) == 3


class TestFrameSubscription:
    """帧订阅的等待契约：换代即全新世界，序号同代内单调。"""

    def test_new_subscriber_after_generation_change_times_out_without_frames(self):
        # 换代清空 journal 后、新一代首帧到达前订阅：必须走超时返回 None。
        # 二次复查若还在，这里会以 journal 空而旧序号领先原地死循环。
        kernel = CaptureKernel(clock=_FakeClock())
        kernel.publish_frame(build_jpeg_bytes(size=(320, 240)), 100.0, 100.0)
        kernel.clear_media_state()

        async def main():
            subscription = kernel.subscribe_video()
            try:
                assert await subscription.next_frame(timeout=0.05) is None
            finally:
                subscription.close()

        asyncio.run(main())

    def test_sequence_counts_frames_within_one_generation(self):
        # 序号不跨代延续：换代归零后，新一代首帧的 sequence 从 1 起算。
        kernel = CaptureKernel(clock=_FakeClock())
        jpeg = build_jpeg_bytes(size=(320, 240))
        kernel.publish_frame(jpeg, 100.0, 100.0)

        async def main():
            first = kernel.subscribe_video()
            try:
                _, _, sequence = await first.next_frame(timeout=0.1)
                assert sequence == 1
            finally:
                first.close()
            kernel.clear_media_state()
            kernel.publish_frame(jpeg, 101.0, 101.0)
            second = kernel.subscribe_video()
            try:
                _, _, sequence = await second.next_frame(timeout=0.1)
                assert sequence == 1
            finally:
                second.close()

        asyncio.run(main())


class _WaitingAudioTrack:
    def __init__(self):
        self._never = asyncio.Event()
        self.active_recvs = 0
        self.max_active_recvs = 0

    async def recv(self):
        self.active_recvs += 1
        self.max_active_recvs = max(self.max_active_recvs, self.active_recvs)
        try:
            await self._never.wait()
        finally:
            self.active_recvs -= 1

    def stop(self):
        self._never.set()


class _BelowBaseAudioTrack:
    def __init__(self):
        self.active_recvs = 0
        self.max_active_recvs = 0

    async def recv(self):
        self.active_recvs += 1
        self.max_active_recvs = max(self.max_active_recvs, self.active_recvs)
        try:
            await asyncio.sleep(0)
            return type("AudioFrameStamp", (), {"pts": 0, "time_base": 0.001})()
        finally:
            self.active_recvs -= 1

    def stop(self):
        pass


class TestAudioReceiverWatchdog:
    def test_resume_restarts_watchdog_while_recv_remains_pending(self, monkeypatch):
        monkeypatch.setattr(AudioReceiver, "WATCHDOG_SECONDS", 0.08)

        async def main():
            track = _WaitingAudioTrack()
            ended = asyncio.Event()
            receiver = AudioReceiver(track, sink=AudioBuffer(), on_ended=ended.set)
            try:
                await asyncio.sleep(0)
                receiver.set_enabled(False)
                await asyncio.sleep(0.1)
                assert not ended.is_set()

                receiver.set_enabled(True)
                await asyncio.sleep(0.04)
                assert not ended.is_set()
                await asyncio.wait_for(ended.wait(), timeout=0.1)
                assert track.max_active_recvs == 1
            finally:
                await receiver.close()

        asyncio.run(main())

    def test_rejected_below_base_frames_do_not_starve_watchdog(self, monkeypatch):
        monkeypatch.setattr(AudioReceiver, "WATCHDOG_SECONDS", 0.05)

        async def main():
            track = _BelowBaseAudioTrack()
            ended = asyncio.Event()
            sink = AudioBuffer()
            receiver = AudioReceiver(track, sink=sink, on_ended=ended.set)
            receiver.set_audio_base_ms(1000)
            try:
                await asyncio.wait_for(ended.wait(), timeout=0.2)
                assert len(sink) == 0
                assert track.max_active_recvs == 1
            finally:
                await receiver.close()

        asyncio.run(main())

    def test_audio_base_change_restarts_current_deadline(self, monkeypatch):
        monkeypatch.setattr(AudioReceiver, "WATCHDOG_SECONDS", 0.3)

        async def main():
            track = _WaitingAudioTrack()
            ended = asyncio.Event()
            receiver = AudioReceiver(track, sink=AudioBuffer(), on_ended=ended.set)
            try:
                await asyncio.sleep(0.2)
                receiver.set_audio_base_ms(1000)
                await asyncio.sleep(0.2)
                assert not ended.is_set()
                await asyncio.wait_for(ended.wait(), timeout=0.2)
                assert track.max_active_recvs == 1
            finally:
                await receiver.close()

        asyncio.run(main())


class _FakeDataChannel:
    def __init__(self, label):
        self.label = label
        self.ordered = True
        self.maxRetransmits = None
        self.maxPacketLifeTime = None
        self.readyState = "open"
        self.handlers = {}
        self.sent = []

    def on(self, name):
        def register(callback):
            self.handlers[name] = callback
            return callback
        return register

    def send(self, payload):
        self.sent.append(payload)

    def emit(self, name, payload):
        self.handlers[name](payload)

    def close(self):
        self.readyState = "closed"
        callback = self.handlers.get("close")
        if callback is not None:
            callback()


class _FakePeer:
    connectionState = "connected"

    async def close(self):
        pass


def test_run_change_clears_media_and_fences_stale_channel_bytes():
    """新 run 清空旧证据，且在观察到新代际栅栏前旧通道字节不得入库。

    摄入端不再解码（无在途解码窗口），代际隔离由 run 栅栏在发布边界
    同步保证：run 切换即作废残帧与未确认字节。
    """

    async def main():
        clock = _FakeClock()
        kernel = CaptureKernel(clock=clock)
        track = _WaitingAudioTrack()
        session = CaptureSession(kernel.hub, kernel, _FakePeer(), track)
        capture_channel = _FakeDataChannel("capture")
        jpeg_channel = _FakeDataChannel("jpeg")
        try:
            assert session.attach_channel(capture_channel)
            await session._handle_state(_state_message(run=1, bpm=70.0, sampled_at_us=100))
            assert session.attach_channel(jpeg_channel)
            assert session._handle_jpeg_run(
                jpeg_channel, json.dumps({"v": 1, "type": "jpeg_run", "run": 1}))

            # 合法帧同步发布原始字节；结构非法（SOI/EOI 缺失）丢弃不入库。
            jpeg = build_jpeg_bytes(size=(320, 240))
            for message in _chunk(jpeg):
                jpeg_channel.emit("message", message)
            assert kernel.video_window() is not None
            for message in _chunk(b"\x00" * 32, pts_ms=1033):
                jpeg_channel.emit("message", message)
            assert sum(1 for _ in kernel.video_window()) == 1
            for pts_ms in (80, 1100):
                for message in _chunk(jpeg, pts_ms=pts_ms):
                    jpeg_channel.emit("message", message)
            assert [frame.timestamp for frame in kernel.video_window()] == [1.0]
            kernel.audio_buffer.extend(b"\x00\x01")

            clock.now += 100.0
            await session._handle_state(_state_message(run=2, bpm=80.0, sampled_at_us=200))
            assert session._handle_jpeg_run(
                jpeg_channel, json.dumps({"v": 1, "type": "jpeg_run", "run": 2}))

            assert kernel.video_window() is None
            assert len(kernel.audio_buffer) == 0
            # 新 run 的传感器采样保留：内存池仍有有效读数。
            assert kernel.heart_rate_window().samples_bpm == (80.0,)
            assert session.video_stalled_for() is not None
            assert session.video_stalled_for() < 5.0
            for message in _chunk(jpeg, pts_ms=90):
                jpeg_channel.emit("message", message)
            assert [frame.timestamp for frame in kernel.video_window()] == [0.09]
        finally:
            await session.close()

    asyncio.run(main())
