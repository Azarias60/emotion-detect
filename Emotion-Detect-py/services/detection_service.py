"""检测业务编排。依赖由组合层注入，不接触 HTTP、全局单例或模型框架。"""
import os
from dataclasses import dataclass
from typing import Callable
from starlette.concurrency import run_in_threadpool


class ModelUnavailableError(RuntimeError):
    """请求所需的模型尚未就绪。"""


@dataclass
class DetectionService:
    models: object
    device: object
    analyze_fatigue: Callable
    analyze_heart_rate: Callable
    transcribe: Callable
    emotion_report: Callable
    emotion_counts: Callable
    health_report: Callable
    fatigue_advice: Callable

    def heart_rate(self, video_path):
        """硬件心率优先，视频提供血压与信噪比。"""
        video_hr = self.analyze_heart_rate(video_path) if video_path else {}
        if not self.device.has_heart_rate_data():
            return video_hr
        mcu_hr = self.device.get_heart_rate_summary()
        return {
            "avg_hr": mcu_hr["avg_hr"],
            "realtime_hr": mcu_hr.get("latest_hr", mcu_hr["avg_hr"]),
            "hr_list": mcu_hr["hr_list"],
            "hrv_indices": (video_hr.get("hrv_indices", mcu_hr["hrv_indices"])
                            if mcu_hr.get("hrv_status") == "unavailable_no_rr_intervals"
                            else mcu_hr["hrv_indices"]),
            "blood_pressure": video_hr.get("blood_pressure", {"systolic": None, "diastolic": None}),
            "signal_quality": "mcu_hardware_sensor",
            "snr_db": video_hr.get("snr_db"),
        }

    async def voice_segments(self, temp_dir):
        return await self.transcribe(
            temp_dir, voice_model=self.models.voice_model, device=self.device
        )

    async def emotion(self, temp_dir, video_path):
        predictor = self.models.predictor
        if predictor is None:
            raise ModelUnavailableError("表情识别模型未加载")
        predictor.reset_log()
        has_video = bool(video_path and os.path.exists(video_path))
        if has_video:
            await run_in_threadpool(predictor.start_capture, video_file=video_path, interval=10)
        emotion_stats = {}
        total_frames = 0
        for entry in predictor.emotion_log:
            emotion = entry["emotion"]
            emotion_stats[emotion] = emotion_stats.get(emotion, 0) + 1
            total_frames += 1
        data = {
            "face_emotion_stats": emotion_stats,
            "total_frames": total_frames,
            "voice_segments": [],
            "physiological": await run_in_threadpool(self.analyze_fatigue, video_path) if has_video else {},
        }
        data["voice_segments"] = await self.voice_segments(temp_dir)
        data["heart_rate"] = await run_in_threadpool(self.heart_rate, video_path)
        data["temperature"] = self.device.get_temperature_summary()
        report = await run_in_threadpool(self.emotion_report, data)
        counts = await run_in_threadpool(self.emotion_counts, report)
        return {**data, "result": report, "emotion_word_count": counts}

    async def health(self, temp_dir, video_path, tongue, questions, answers):
        physiological = self.analyze_fatigue(video_path) if video_path else {}
        voice_segments = await self.voice_segments(temp_dir)
        heart_rate = self.heart_rate(video_path)
        temperature = self.device.get_temperature_summary()
        report = self.health_report(tongue, questions, answers, physiological, heart_rate, temperature, voice_segments=voice_segments)
        return {
            "tongue": tongue, "questions": questions, "answers": answers,
            "voice_segments": voice_segments, "physiological": physiological,
            "heart_rate": heart_rate, "temperature": temperature, "result": report,
        }

    def fatigue(self, video_path):
        physiological = self.analyze_fatigue(video_path)
        return {
            "physiological": physiological,
            "heart_rate": self.heart_rate(video_path),
            "advice": self.fatigue_advice(physiological),
        }
