"""FacePhys 视频生理测量的结果视图。

每个能力字段显式缺失（``None``）；诊断只携带判定与窗口证据。
数值仅允许有限值；比例类字段限制在 [0, 1]。
"""
from dataclasses import dataclass, field
from math import isfinite
from typing import Literal


def _finite(**values):
    for name, value in values.items():
        if value is not None and not isfinite(value):
            raise ValueError(f"{name} must be finite")


def _unit_interval(name, value):
    if value is not None and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class VideoHrvMeasurement:
    """上游脉搏间期指标；未发布的证据字段保留为缺测。"""

    rmssd_ms: float | None = None
    sdnn_ms: float | None = None
    nn50: int | None = None
    pnn50_percent: float | None = None
    lf_hf_ratio: float | None = None
    rr_interval_count: int | None = None

    def __post_init__(self):
        _finite(rmssd_ms=self.rmssd_ms, sdnn_ms=self.sdnn_ms,
                pnn50_percent=self.pnn50_percent, lf_hf_ratio=self.lf_hf_ratio)
        if (self.rr_interval_count is not None and self.rr_interval_count < 0
                or self.nn50 is not None and self.nn50 < 0):
            raise ValueError("negative interval count")
        has_indices = any(value is not None for value in (
            self.rmssd_ms, self.sdnn_ms, self.nn50, self.pnn50_percent, self.lf_hf_ratio,
        ))
        if has_indices and self.rr_interval_count == 0:
            raise ValueError("HRV index values require RR intervals")


PulseIntervalStatus = Literal["available", "no_video", "no_signal", "collecting", "low_signal_quality", "unavailable"]


@dataclass(frozen=True)
class PhysiologyDiagnostics:
    hrv_status: PulseIntervalStatus
    respiration_status: PulseIntervalStatus
    signal_quality: float | None
    window_seconds: float


@dataclass(frozen=True)
class VideoPhysiologyMeasurement:
    """一次视频测量的生理结果；缺测字段为 ``None``。"""

    heart_rate_bpm: float | None = None
    respiration_rate_per_min: float | None = None
    hrv: VideoHrvMeasurement | None = None
    sqi: float | None = None
    face_coverage: float | None = None
    diagnostics: PhysiologyDiagnostics | None = None
    source: Literal["video"] = field(default="video", init=False)

    def __post_init__(self):
        _finite(heart_rate_bpm=self.heart_rate_bpm,
                respiration_rate_per_min=self.respiration_rate_per_min)
        _unit_interval("sqi", self.sqi)
        _unit_interval("face_coverage", self.face_coverage)
        if self.heart_rate_bpm is not None and self.heart_rate_bpm <= 0:
            raise ValueError("heart_rate_bpm must be positive")
        if (self.respiration_rate_per_min is not None
                and self.respiration_rate_per_min <= 0):
            raise ValueError("respiration_rate_per_min must be positive")
