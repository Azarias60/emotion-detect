"""旧版服务导入的兼容入口；新代码应使用各职责对应的服务模块。"""
from core.state import models, tongue_predictor_lock
from mcu import mcu_client
from services.media_service import write_recorded_video, write_recorded_audio, resolve_session_audio
from services.physiology_service import analyze_video_fatigue, analyze_video_heart_rate
from services.speech_service import split_and_clean_voice_by_two_sentences, transcribe_recorded_audio
from services.tongue_service import TONGUE_LABELS, inspect_tongue_image
from services.tongue_service import analyze_tongue_image as _analyze_tongue_image


def _write_recorded_video(temp_dir, filename="recorded.mp4"):
    return write_recorded_video(temp_dir, filename, device=mcu_client)


def _write_recorded_audio(temp_dir, base_filename="mcu_audio"):
    return write_recorded_audio(temp_dir, base_filename, device=mcu_client)


def _resolve_session_audio(temp_dir):
    return resolve_session_audio(temp_dir, device=mcu_client)


async def _transcribe_recorded_audio(temp_dir):
    return await transcribe_recorded_audio(temp_dir, voice_model=models.voice_model, device=mcu_client)


def analyze_tongue_image(image_path):
    return _analyze_tongue_image(image_path, predictor=models.tongue_predictor, lock=tongue_predictor_lock)
