# -*-coding: utf-8 -*-
"""``capture`` 通道传感器状态的接收与窗口化。

设备每 500ms 上报一次 ``state`` 快照（MLX90614 环境温/目标温、LD6002 雷达
心率，各自带 valid 标志与 ``sampled_at_us``）。本模块：
- 只接受有效（valid）测量进入采样窗口；
- 以接收时刻维护窗口（统一采集窗口，与音频/视频同源 / 实时窗口 10 秒），
  供领域端口 ``HeartRateWindow`` / ``TemperatureWindow`` 投影；
- 3 秒新鲜度判定用于诊断状态。
"""

import math
import time
from collections import deque

from server import settings
from server.capture.domain import HeartRateWindow, TemperatureWindow
from server.capture.webrtc.protocol import run_is_older

SENSOR_FRESH_SECONDS = 3.0
LIVE_WINDOW_SECONDS = 10.0


def _empty_sensor(measurement_fields):
    return {
        "received": False,
        "valid": False,
        "stale": False,
        "sampled_at_us": None,
        "age_ms": None,
        **{field: None for field in measurement_fields},
        "error": None,
    }


class _Series:
    """单指标窗口采样：元素为 (received_monotonic, value)。"""

    def __init__(self):
        self._samples: deque[tuple[float, float]] = deque()

    def expire(self, now):
        cutoff = now - settings.CAPTURE_WINDOW_SECONDS
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def append(self, now, value):
        self.expire(now)
        self._samples.append((now, float(value)))

    def clear(self):
        self._samples.clear()

    def values(self, window_seconds=None, now=None):
        if now is not None:
            self.expire(now)
        if window_seconds is None:
            return tuple(value for _, value in self._samples)
        cutoff = (now if now is not None else time.monotonic()) - window_seconds
        return tuple(value for received, value in self._samples if received >= cutoff)

    def latest(self, now=None):
        if now is not None:
            self.expire(now)
        return self._samples[-1][1] if self._samples else None


class SensorState:
    """传感器最新状态与窗口采样；由采集会话按 ``state`` 消息驱动。"""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self.run = 0
        self._bpm = _Series()
        self._ambient = _Series()
        self._object = _Series()
        self._temperature_record: dict | None = None
        self._heart_rate_record: dict | None = None

    def new_window(self, clock):
        """Start empty series, retaining device sample IDs for deduplication."""
        window = SensorState(clock=clock)
        window.run = self.run
        window._temperature_record = self._temperature_record
        window._heart_rate_record = self._heart_rate_record
        return window

    def reset(self, run=None):
        """新会话或新代际开始：清空窗口，旧数据不得混入新会话证据。"""
        if run is not None:
            self.run = run
        self._bpm.clear()
        self._ambient.clear()
        self._object.clear()
        self._temperature_record = None
        self._heart_rate_record = None

    def accept_state(self, message) -> bool:
        """应用一条已解析的 ``state`` 快照；旧代际返回 False，新代际重置窗口。

        JSON 解析与 wire 校验归协议层（``parse_capture_message``），本类只
        接收通过校验的消息字典。
        """
        run = message["run"]
        if run != self.run:
            if run_is_older(run, self.run):
                return False
            self.reset(run)
        now = self._clock()
        self._temperature_record = self._apply_sensor(
            message["temperature"], now,
            {"ambient_c": self._ambient, "object_c": self._object},
            self._temperature_record,
        )
        self._heart_rate_record = self._apply_sensor(
            message["heart_rate"], now, {"bpm": self._bpm},
            self._heart_rate_record,
        )
        return True

    @staticmethod
    def _apply_sensor(record, now, series_map, previous) -> dict | None:
        """记录最新快照并把 valid 测量写入对应窗口序列。"""
        sampled_at_us = record["sampled_at_us"]
        if sampled_at_us is None:
            return None
        is_new_sample = previous is None or previous["sampled_at_us"] != sampled_at_us
        if record["valid"] and is_new_sample:
            for field, series in series_map.items():
                series.append(now, record[field])
        return {
            "received_at": now if is_new_sample else previous["received_at"],
            **{key: value for key, value in record.items()},
        }

    # ------------------------------------------------------------------
    # 领域端口投影（时序切片：无采样数据统一返回 None）
    # ------------------------------------------------------------------
    def heart_rate_window(self, seconds: float | None = None) -> HeartRateWindow | None:
        now = self._clock()
        samples = self._bpm.values(seconds, now=now)
        if not samples:
            return None
        return HeartRateWindow(
            samples_bpm=samples,
            average_bpm=(math.fsum(samples) / len(samples)),
            latest_bpm=samples[-1],
            live_samples_bpm=self._bpm.values(LIVE_WINDOW_SECONDS, now=now),
        )

    def temperature_window(self, seconds: float | None = None) -> TemperatureWindow | None:
        now = self._clock()
        ambient = self._ambient.values(seconds, now=now)
        objects = self._object.values(seconds, now=now)
        if not ambient and not objects:
            return None
        return TemperatureWindow(
            ambient_temps=ambient,
            object_temps=objects,
            avg_ambient_temp=(math.fsum(ambient) / len(ambient)) if ambient else None,
            avg_object_temp=(math.fsum(objects) / len(objects)) if objects else None,
            latest_ambient_temp=ambient[-1] if ambient else None,
            latest_object_temp=objects[-1] if objects else None,
        )

    def _sensor_status(self, record, measurement_fields, now):
        if record is None:
            return _empty_sensor(measurement_fields)
        age = max(0.0, now - record["received_at"])
        stale = age > SENSOR_FRESH_SECONDS
        return {
            "received": True,
            "valid": bool(record["valid"] and not stale),
            "stale": stale,
            "sampled_at_us": record["sampled_at_us"],
            "age_ms": age * 1000,
            **{field: record.get(field) for field in measurement_fields},
            "error": record.get("error"),
        }

    def status(self) -> dict:
        now = self._clock()
        self._bpm.expire(now)
        self._ambient.expire(now)
        self._object.expire(now)
        return {
            "temperature": self._sensor_status(
                self._temperature_record, ("ambient_c", "object_c"), now),
            "heart_rate": self._sensor_status(
                self._heart_rate_record, ("bpm",), now),
        }


__all__ = ["SENSOR_FRESH_SECONDS", "SensorState"]
