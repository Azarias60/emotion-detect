"""供情绪、健康报告与疲劳等 HTTP 入口共享的事实类型和纯投影。

每种观测数据（行为、疲劳、FacePhys 生理测量、语音）只维护一份字段选择
规则。两个报告域直接引用这里的 TypedDict；网关在此基础上追加
HTTP 专有字段、显示命名和 JSON 数组转换，不复制公共事实字段定义。
领域专属的表情、舌象与体质投影分别保留在各自 analysis 模块。
"""

from typing import Literal

from typing_extensions import TypedDict

from server.compute.facephys.types import VideoHrvMeasurement, VideoPhysiologyMeasurement
from server.compute.fatigue.types import BehaviorObservation, FatigueAdvice, FatigueAssessment
from server.compute.speech.types import Transcript


# --- 共享事实词表 -------------------------------------------------------------


class BehaviorObservationFact(TypedDict):
    blink_count: int
    blink_frequency: float
    yawn_count: int
    nod_count: int
    nod_frequency: float
    perclos_percent: float
    speaking_percent: float
    face_coverage: float


class FatigueAssessmentFact(TypedDict):
    score: int
    level: str


class FatigueAdviceFact(TypedDict):
    level: str
    text: str


class HRVMeasurementFact(TypedDict):
    sdnn_ms: float | None
    rmssd_ms: float | None
    nn50: int | None
    pnn50_percent: float | None
    lf_hf_ratio: float | None
    rr_interval_count: int | None
    rr_source: Literal['rppg_peak_derived']


class VideoPhysiologyFact(TypedDict):
    heart_rate_bpm: float | None
    respiration_rate_per_min: float | None
    hrv: HRVMeasurementFact | None


class SpeechSegmentFact(TypedDict):
    text: str


class SpeechFact(TypedDict):
    segments: list[SpeechSegmentFact]


# --- 共享投影 -----------------------------------------------------------------


def behavior_observation_fact(value: BehaviorObservation | None) -> BehaviorObservationFact | None:
    if value is None:
        return None
    return {
        'blink_count': value.blink_count,
        'blink_frequency': value.blink_frequency,
        'yawn_count': value.yawn_count,
        'nod_count': value.nod_count,
        'nod_frequency': value.nod_frequency,
        'perclos_percent': value.perclos_percent,
        'speaking_percent': value.speaking_percent,
        'face_coverage': value.face_coverage,
    }


def fatigue_assessment_fact(value: FatigueAssessment | None) -> FatigueAssessmentFact | None:
    if value is None:
        return None
    return {
        'score': value.score,
        'level': value.level,
    }


def fatigue_advice_fact(value: FatigueAdvice) -> FatigueAdviceFact:
    return {
        'level': value.level,
        'text': value.text,
    }


def hrv_measurement_fact(value: VideoHrvMeasurement | None) -> HRVMeasurementFact | None:
    if value is None:
        return None
    return {
        'sdnn_ms': value.sdnn_ms,
        'rmssd_ms': value.rmssd_ms,
        'nn50': value.nn50,
        'pnn50_percent': value.pnn50_percent,
        'lf_hf_ratio': value.lf_hf_ratio,
        'rr_interval_count': value.rr_interval_count,
        'rr_source': 'rppg_peak_derived',
    }


def video_physiology_fact(value: VideoPhysiologyMeasurement | None) -> VideoPhysiologyFact | None:
    if value is None or (
        value.heart_rate_bpm is None and value.respiration_rate_per_min is None and value.hrv is None
    ):
        return None
    return {
        'heart_rate_bpm': value.heart_rate_bpm,
        'respiration_rate_per_min': value.respiration_rate_per_min,
        'hrv': hrv_measurement_fact(value.hrv),
    }


def speech_fact(value: Transcript | None) -> SpeechFact | None:
    if value is None:
        return None
    return {"segments": [{"text": segment.text} for segment in value.segments]}
