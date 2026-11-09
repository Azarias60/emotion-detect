"""生产依赖组合入口；路由可通过 FastAPI dependency_overrides 替换服务。"""
from core.state import models, tongue_predictor_lock
from mcu import mcu_client
from services import ai_service
from services.detection_service import DetectionService
from services.physiology_service import analyze_video_fatigue, analyze_video_heart_rate
from services.speech_service import transcribe_recorded_audio
from services.tongue_service import analyze_tongue_image


def get_detection_service() -> DetectionService:
    return DetectionService(
        models=models,
        device=mcu_client,
        analyze_fatigue=analyze_video_fatigue,
        analyze_heart_rate=analyze_video_heart_rate,
        transcribe=transcribe_recorded_audio,
        emotion_report=ai_service.generate_emotion_analysis_report,
        emotion_counts=ai_service.extract_emotion_count_from_report,
        health_report=ai_service.generate_health_analysis_report,
        fatigue_advice=ai_service.generate_fatigue_advice,
    )


def get_tongue_analyzer():
    def analyze(image_path):
        return analyze_tongue_image(
            image_path, predictor=models.tongue_predictor, lock=tongue_predictor_lock
        )
    return analyze


def get_device():
    return mcu_client


def get_hardware_service():
    from services.hardware_service import HardwareService
    return HardwareService(get_device())
