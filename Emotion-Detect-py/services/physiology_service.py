"""行为疲劳与心率算法适配；仅在实际推理时导入模型。"""
import os

from core.config import BP_MODEL_PATH, LANDMARK_MODEL_PATH
from core.logger import get_logger

logger = get_logger(__name__)


def analyze_video_fatigue(video_path: str) -> dict:
    """调用行为特征分析模型评估视频疲劳程度。"""
    if not video_path or not os.path.exists(video_path):
        return {
            "blink_count": None, "blink_frequency": None,
            "yawn_count": None, "nod_count": None, "nod_frequency": None,
            "avg_perclos": None, "speaking_ratio": None,
            "fatigue_score": None, "fatigue_probability": None,
            "fatigue_level": "数据缺失", "status": "missing_video",
            "face_coverage": 0.0, "valid": False,
        }
    from detection_models.fatigue_v03 import analyze_fatigue_video

    return analyze_fatigue_video(video_path, LANDMARK_MODEL_PATH)


def analyze_video_heart_rate(video_path: str) -> dict:
    """基于 KDE-HRV 与 rPPG 模型估算视频心率与血压。"""
    empty = {
        "avg_hr": None, "realtime_hr": None, "hr_list": [], "snr_db": None,
        "signal_quality": "missing_video",
        "hrv_indices": {"SDNN": None, "RMSSD": None, "NN50": None, "pNN50": None},
        "blood_pressure": {"systolic": None, "diastolic": None, "status": "model_missing"},
        "face_coverage": 0.0,
    }
    if not video_path or not os.path.exists(video_path):
        return empty
    try:
        from detection_models.kde_hrv import analyze_hrv_video

        return analyze_hrv_video(video_path, BP_MODEL_PATH)
    except Exception as error:
        logger.warning("KDE-HRV 分析不可用：%s", error)
        return {**empty, "signal_quality": "unavailable", "error": str(error)}
