"""Importable adaptation of ``KDE-HRV.py`` for uploaded browser videos."""

import threading
from collections import deque
from pathlib import Path

import cv2
import numpy as np
from scipy.signal import butter, detrend, filtfilt, find_peaks, resample
from scipy.stats import gaussian_kde

HR_MIN_BPM = 50.0
HR_MAX_BPM = 130.0
SNR_THRESHOLD_DB = -3.0
MIN_DATA_SECONDS = 6.0
REALTIME_WINDOW_SECONDS = 10.0
ROI_PAD = 10


def _bandpass(signal, fs, low_hz=0.75, high_hz=3.0, order=3):
    values = np.asarray(signal, dtype=np.float64)
    if len(values) < 20 or fs <= 2 * high_hz:
        return values.copy()
    b, a = butter(order, [low_hz / (fs / 2.0), high_hz / (fs / 2.0)], btype="band")
    padlen = 3 * max(len(a), len(b))
    return filtfilt(b, a, values) if len(values) > padlen else values.copy()


def _pos_rppg(rgb_values):
    rgb = np.asarray(rgb_values, dtype=np.float64)
    if len(rgb) < 3:
        return np.zeros(len(rgb), dtype=np.float64)
    normalized = rgb / (np.mean(rgb, axis=0) + 1e-8) - 1.0
    red, green, blue = normalized[:, 0], normalized[:, 1], normalized[:, 2]
    first = green - red
    second = green + red - 2.0 * blue
    return first + (np.std(first) / (np.std(second) + 1e-8)) * second


def _uniform(timestamps, signal):
    times = np.asarray(timestamps, dtype=np.float64)
    values = np.asarray(signal, dtype=np.float64)
    if len(times) < 4 or len(times) != len(values):
        return None, None
    keep = np.concatenate(([True], np.diff(times) > 1e-6))
    times, values = times[keep], values[keep]
    if len(times) < 4 or times[-1] <= times[0]:
        return None, None
    fs = float(np.clip(1.0 / np.median(np.diff(times)), 10.0, 60.0))
    count = max(int((times[-1] - times[0]) * fs) + 1, 4)
    uniform_times = np.linspace(times[0], times[-1], count)
    return np.interp(uniform_times, times, values), fs


def _fft_hr(signal, fs):
    values = np.asarray(signal, dtype=np.float64)
    if len(values) < max(int(fs * MIN_DATA_SECONDS), 64):
        return None, None
    values = detrend(values)
    std = np.std(values)
    if std < 1e-10:
        return None, None
    values = (values - np.mean(values)) / std
    power = np.abs(np.fft.rfft(values * np.hamming(len(values)))) ** 2
    frequencies = np.fft.rfftfreq(len(values), d=1.0 / fs)
    mask = (frequencies >= HR_MIN_BPM / 60.0) & (frequencies <= HR_MAX_BPM / 60.0)
    if not np.any(mask):
        return None, None
    band_frequencies, band_power = frequencies[mask], power[mask]
    peak_frequency = float(band_frequencies[int(np.argmax(band_power))])
    signal_mask = np.abs(band_frequencies - peak_frequency) <= 0.10
    signal_power = max(float(np.sum(band_power[signal_mask])), 1e-12)
    noise_power = max(float(np.sum(band_power[~signal_mask])), 1e-12)
    return peak_frequency * 60.0, 10.0 * np.log10(signal_power / noise_power)


def _hrv(signal, fs):
    peaks, _ = find_peaks(signal, distance=fs / (HR_MAX_BPM / 60.0))
    if len(peaks) < 3:
        return {"SDNN": None, "RMSSD": None, "NN50": None, "pNN50": None}
    intervals = np.diff(peaks / fs * 1000.0)
    differences = np.diff(intervals)
    nn50 = int(np.sum(np.abs(differences) > 50))
    return {
        "SDNN": round(float(np.std(intervals)), 2),
        "RMSSD": round(float(np.sqrt(np.mean(differences ** 2))), 2) if len(differences) else None,
        "NN50": nn50,
        "pNN50": round(nn50 / len(differences) * 100.0, 2) if len(differences) else None,
    }


def _kde_value(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return None
    if len(values) < 3 or np.ptp(values) < 1e-6:
        return float(np.median(values))
    density = gaussian_kde(values, bw_method="scott")
    domain = np.linspace(values.min() - 5, values.max() + 5, 1000)
    return float(domain[int(np.argmax(density(domain)))])


_FACE_MESH_CACHE = {}
_FACE_MESH_LOCK = threading.Lock()
_BP_MODEL_CACHE = {}


def _get_face_mesh():
    """进程级缓存 mediapipe FaceMesh（初始化约数百毫秒），避免每请求重建。

    当前分析端点为事件循环内的同步调用，串行使用共享实例；跨视频的跟踪状态
    残留可忽略（FaceMesh 会在无人脸时自动重置）。
    """
    if "mesh" not in _FACE_MESH_CACHE:
        with _FACE_MESH_LOCK:
            if "mesh" not in _FACE_MESH_CACHE:
                try:
                    import mediapipe as mp
                except ImportError as error:
                    raise RuntimeError("缺少 mediapipe，无法提取额头心率信号") from error
                _FACE_MESH_CACHE["mesh"] = mp.solutions.face_mesh.FaceMesh(
                    static_image_mode=False, max_num_faces=1, refine_landmarks=True,
                    min_detection_confidence=0.5, min_tracking_confidence=0.5,
                )
    return _FACE_MESH_CACHE["mesh"]


def _get_bp_model(bp_model_path):
    """按路径缓存血压模型（tf.keras）。返回 (model, status)，文件缺失为 model_missing。"""
    if not bp_model_path:
        return None, "model_missing"
    key = str(bp_model_path)
    if key not in _BP_MODEL_CACHE:
        if not Path(key).exists():
            _BP_MODEL_CACHE[key] = (None, "model_missing")
        else:
            try:
                import tensorflow as tf
                _BP_MODEL_CACHE[key] = (tf.keras.models.load_model(key), "ready")
            except Exception:
                _BP_MODEL_CACHE[key] = (None, "load_failed")
    return _BP_MODEL_CACHE[key]


class RPPGAnalyzer:
    def __init__(self, fps, bp_model_path=None):
        self.face_mesh = _get_face_mesh()
        self.fps = max(float(fps or 30.0), 1.0)
        self.timestamps = []
        self.rgb_values = []
        self.hr_samples = []
        self.snr_samples = []
        self.last_estimate = -1.0
        self.frames = 0
        self.face_frames = 0
        self.bp_model, self.bp_model_status = _get_bp_model(bp_model_path)

    def _forehead(self, frame):
        result = self.face_mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        if not result.multi_face_landmarks:
            return None
        height, width = frame.shape[:2]
        landmarks = result.multi_face_landmarks[0].landmark
        points = np.array([[int(landmarks[index].x * width), int(landmarks[index].y * height)] for index in [10, 67, 103, 109, 338, 297]])
        x1, y1 = np.min(points, axis=0)
        x2, y2 = np.max(points, axis=0)
        x1, y1 = max(int(x1 - ROI_PAD), 0), max(int(y1 - ROI_PAD), 0)
        x2, y2 = min(int(x2 + ROI_PAD), width - 1), min(int(y2 + ROI_PAD), height - 1)
        roi = frame[y1:y2, x1:x2]
        return roi if roi.size else None

    def _estimate(self, timestamps, rgb_values):
        raw = _pos_rppg(rgb_values)
        uniform, fs = _uniform(timestamps, raw)
        if uniform is None:
            return None, None, None, None
        filtered = _bandpass(uniform, fs, HR_MIN_BPM / 60.0, HR_MAX_BPM / 60.0)
        heart_rate, snr = _fft_hr(filtered, fs)
        return heart_rate, snr, filtered, fs

    def process_frame(self, frame, timestamp):
        self.frames += 1
        roi = self._forehead(frame)
        if roi is None:
            return
        self.face_frames += 1
        mean_bgr = np.mean(roi.reshape(-1, 3), axis=0)
        self.timestamps.append(float(timestamp))
        self.rgb_values.append([mean_bgr[2], mean_bgr[1], mean_bgr[0]])
        if timestamp - self.last_estimate < 1.0:
            return
        cutoff = timestamp - REALTIME_WINDOW_SECONDS
        start = next((index for index, value in enumerate(self.timestamps) if value >= cutoff), 0)
        heart_rate, snr, _, _ = self._estimate(self.timestamps[start:], self.rgb_values[start:])
        if heart_rate is not None and snr is not None and HR_MIN_BPM <= heart_rate <= HR_MAX_BPM:
            self.hr_samples.append(float(heart_rate))
            self.snr_samples.append(float(snr))
        self.last_estimate = timestamp

    def result(self):
        heart_rate, snr, filtered, fs = self._estimate(self.timestamps, self.rgb_values)
        valid_heart_rates = [value for value, quality in zip(self.hr_samples, self.snr_samples) if quality >= SNR_THRESHOLD_DB]
        stable = _kde_value(valid_heart_rates) or heart_rate
        hrv = _hrv(filtered, fs) if filtered is not None else {"SDNN": None, "RMSSD": None, "NN50": None, "pNN50": None}
        systolic = diastolic = None
        if self.bp_model is not None and filtered is not None and len(filtered):
            try:
                normalized = resample(filtered, 875)
                normalized = (normalized - np.mean(normalized)) / (np.std(normalized) + 1e-8)
                prediction = self.bp_model.predict(normalized.reshape(1, 875, 1), verbose=0)
                systolic, diastolic = float(prediction[0][0][0]), float(prediction[1][0][0])
            except Exception:
                self.bp_model_status = "prediction_failed"
        quality = "good" if snr is not None and snr >= SNR_THRESHOLD_DB else "poor" if self.face_frames else "no_face"
        return {
            "hr_list": [round(value, 2) for value in valid_heart_rates],
            "avg_hr": round(float(stable), 2) if stable is not None else None,
            "realtime_hr": round(float(heart_rate), 2) if heart_rate is not None else None,
            "snr_db": round(float(snr), 2) if snr is not None else None,
            "signal_quality": quality,
            "hrv_indices": hrv,
            "blood_pressure": {
                "systolic": round(systolic, 2) if systolic is not None else None,
                "diastolic": round(diastolic, 2) if diastolic is not None else None,
                "status": self.bp_model_status,
            },
            "face_coverage": round(self.face_frames / max(self.frames, 1), 4),
        }

def analyze_hrv_video(video_path, bp_model_path=None, sample_every=1):
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError("无法打开心率检测视频")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    analyzer = RPPGAnalyzer(fps=fps, bp_model_path=bp_model_path)
    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % max(int(sample_every), 1) == 0:
                analyzer.process_frame(frame, frame_index / fps)
            frame_index += 1
        return analyzer.result()
    finally:
        capture.release()
