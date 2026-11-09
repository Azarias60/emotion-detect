"""Importable adaptation of ``8.2x-ver0.3.py``.

The original desktop script opened camera 0 and rendered an OpenCV window.  The
web application already owns the computer camera, so this module applies the
same EAR/MAR/PERCLOS/head-pose rules to the video uploaded by the browser.
"""

import math
import threading
from collections import deque
from pathlib import Path

import cv2
import dlib
import numpy as np
from scipy.spatial import distance

EYE_AR_THRESH = 0.23
EYE_AR_CONSEC_FRAMES = 2
SPEAK_MAR_LOW = 0.20
SPEAK_MAR_HIGH = 0.58
SPEAK_DYNAMIC_WINDOW = 1.0
SPEAK_MIN_RANGE = 0.045
SPEAK_MIN_SPEED = 0.0025
SPEAK_MIN_TRANSITIONS = 2
SPEAK_HOLD_SEC = 0.55
YAWN_MAR_THRESH = 0.62
YAWN_CONSEC_FRAMES = 30
YAWN_RELEASE_FRAMES = 5
HAR_THRESH = 5
NOD_CONSEC_FRAMES = 10
FATIGUE_PERCLOS_THRESH = 20.0
FATIGUE_BLINK_FREQ_THRESH = 5
FATIGUE_YAWN_THRESH = 3
FATIGUE_NOD_FREQ_THRESH = 2

OBJECT_POINTS = np.float32([
    [6.825897, 6.760612, 4.402142], [1.330353, 7.122144, 6.903745],
    [-1.330353, 7.122144, 6.903745], [-6.825897, 6.760612, 4.402142],
    [5.311432, 5.485328, 3.987654], [1.789930, 5.393625, 4.413414],
    [-1.789930, 5.393625, 4.413414], [-5.311432, 5.485328, 3.987654],
    [2.005628, 1.409845, 6.165652], [-2.005628, 1.409845, 6.165652],
    [2.774015, -2.080775, 5.048531], [-2.774015, -2.080775, 5.048531],
    [0.0, -3.116408, 6.097667], [0.0, -7.415691, 4.070434],
])
CAMERA_MATRIX = np.array([
    653.0839199346667, 0.0, 319.5, 0.0, 653.0839199346667, 239.5, 0.0, 0.0, 1.0
], dtype=np.float32).reshape(3, 3)
DIST_COEFFS = np.array([0.07083463368440736, 0.06914019373753535, 0.0, 0.0, -1.3073460323689292]).reshape(5, 1)


def _eye_aspect_ratio(eye):
    a = distance.euclidean(eye[1], eye[5])
    b = distance.euclidean(eye[2], eye[4])
    c = distance.euclidean(eye[0], eye[3])
    return (a + b) / max(2.0 * c, 1e-8)


def _mouth_aspect_ratio(mouth):
    a = distance.euclidean(mouth[2], mouth[10])
    b = distance.euclidean(mouth[4], mouth[8])
    c = distance.euclidean(mouth[0], mouth[6])
    return (a + b) / max(2.0 * c, 1e-8)


def _head_pitch(shape):
    image_points = np.float32([
        shape[17], shape[21], shape[22], shape[26], shape[36], shape[39], shape[42],
        shape[45], shape[31], shape[35], shape[48], shape[54], shape[57], shape[8],
    ])
    ok, rotation, translation = cv2.solvePnP(
        OBJECT_POINTS, image_points, CAMERA_MATRIX, DIST_COEFFS, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        return 0.0
    rotation_matrix, _ = cv2.Rodrigues(rotation)
    pose_matrix = cv2.hconcat((rotation_matrix, translation))
    *_, euler = cv2.decomposeProjectionMatrix(pose_matrix)
    return math.degrees(math.asin(math.sin(math.radians(float(euler[0, 0])))))


_LANDMARK_TOOLS_CACHE = {}
_LANDMARK_TOOLS_LOCK = threading.Lock()


def _get_landmark_tools(landmark_path):
    """进程级缓存 dlib 检测器与 95MB 关键点模型，避免每次请求重新加载。

    当前分析端点均为 async 内的同步调用（事件循环单线程执行），使用无需加锁；
    加载锁仅保证多线程首次加载不重复。
    """
    key = str(landmark_path)
    tools = _LANDMARK_TOOLS_CACHE.get(key)
    if tools is None:
        with _LANDMARK_TOOLS_LOCK:
            tools = _LANDMARK_TOOLS_CACHE.get(key)
            if tools is None:
                path = Path(landmark_path)
                if not path.exists():
                    raise FileNotFoundError(f"疲劳检测关键点模型不存在：{path}")
                tools = (dlib.get_frontal_face_detector(), dlib.shape_predictor(key))
                _LANDMARK_TOOLS_CACHE[key] = tools
    return tools


class FatigueAnalyzer:
    def __init__(self, landmark_path, fps=30.0):
        self.detector, self.predictor = _get_landmark_tools(landmark_path)
        self.fps = max(float(fps or 30.0), 1.0)
        self.eye_closed_frames = 0
        self.blink_count = 0
        self.yawn_frames = 0
        self.yawn_count = 0
        self.yawn_active = False
        self.yawn_release = 0
        self.nod_frames = 0
        self.nod_count = 0
        self.speaking = False
        self.last_speaking_time = 0.0
        self.previous_mouth_open = False
        self.transitions = deque()
        self.mar_history = deque()
        self.mar_smooth = deque(maxlen=5)
        self.closed_samples = []
        self.speaking_samples = []
        self.frames = 0
        self.face_frames = 0

    def process_frame(self, frame, timestamp):
        self.frames += 1
        frame = cv2.resize(frame, (640, 480))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.detector(gray, 0)
        if not faces:
            self.speaking = False
            self.closed_samples.append(0)
            self.speaking_samples.append(0)
            return

        self.face_frames += 1
        landmarks = self.predictor(gray, faces[0])
        shape = np.array([[point.x, point.y] for point in landmarks.parts()], dtype=np.float32)
        left_eye, right_eye, mouth = shape[36:42], shape[42:48], shape[48:68]
        ear = (_eye_aspect_ratio(left_eye) + _eye_aspect_ratio(right_eye)) / 2.0
        mar = _mouth_aspect_ratio(mouth)
        closed = ear < EYE_AR_THRESH
        if closed:
            self.eye_closed_frames += 1
        else:
            if self.eye_closed_frames >= EYE_AR_CONSEC_FRAMES:
                self.blink_count += 1
            self.eye_closed_frames = 0

        yawn_frame_limit = max(int(self.fps), YAWN_CONSEC_FRAMES)
        if mar >= YAWN_MAR_THRESH:
            self.yawn_frames += 1
            self.yawn_release = 0
            if self.yawn_frames >= yawn_frame_limit and not self.yawn_active:
                self.yawn_count += 1
                self.yawn_active = True
        elif self.yawn_active:
            self.yawn_release += 1
            if self.yawn_release >= YAWN_RELEASE_FRAMES:
                self.yawn_active = False
                self.yawn_frames = 0
                self.yawn_release = 0
        else:
            self.yawn_frames = 0

        self.mar_smooth.append(mar)
        smooth_mar = float(np.mean(self.mar_smooth))
        self.mar_history.append((timestamp, smooth_mar))
        while self.mar_history and timestamp - self.mar_history[0][0] > SPEAK_DYNAMIC_WINDOW:
            self.mar_history.popleft()
        open_threshold = SPEAK_MAR_LOW + 0.04
        close_threshold = SPEAK_MAR_LOW - 0.01
        mouth_open = smooth_mar > close_threshold if self.previous_mouth_open else smooth_mar >= open_threshold
        if mouth_open != self.previous_mouth_open:
            self.transitions.append(timestamp)
            self.previous_mouth_open = mouth_open
        while self.transitions and timestamp - self.transitions[0] > SPEAK_DYNAMIC_WINDOW:
            self.transitions.popleft()
        values = np.array([value for _, value in self.mar_history], dtype=float)
        mar_range = float(np.ptp(values)) if len(values) >= 4 else 0.0
        mar_speed = float(np.mean(np.abs(np.diff(values)))) if len(values) >= 4 else 0.0
        dynamic_score = sum((mar_range >= SPEAK_MIN_RANGE, mar_speed >= SPEAK_MIN_SPEED, len(self.transitions) >= SPEAK_MIN_TRANSITIONS))
        yawn_like = self.yawn_active or smooth_mar >= YAWN_MAR_THRESH
        if not yawn_like and SPEAK_MAR_LOW <= smooth_mar < SPEAK_MAR_HIGH and dynamic_score >= 2:
            self.speaking = True
            self.last_speaking_time = timestamp
        elif self.speaking and (yawn_like or timestamp - self.last_speaking_time > SPEAK_HOLD_SEC):
            self.speaking = False

        pitch = _head_pitch(shape)
        if pitch > HAR_THRESH:
            self.nod_frames += 1
            if self.nod_frames >= NOD_CONSEC_FRAMES:
                self.nod_count += 1
                self.nod_frames = 0
        else:
            self.nod_frames = 0
        self.closed_samples.append(1 if closed else 0)
        self.speaking_samples.append(1 if self.speaking and not yawn_like else 0)

    def result(self, duration):
        duration = max(float(duration), 1.0)
        perclos_percent = float(np.mean(self.closed_samples) * 100.0) if self.closed_samples else 0.0
        speaking_percent = float(np.mean(self.speaking_samples) * 100.0) if self.speaking_samples else 0.0
        blink_frequency = self.blink_count * 60.0 / duration
        nod_frequency = self.nod_count * 60.0 / duration
        score = sum((
            perclos_percent >= FATIGUE_PERCLOS_THRESH,
            blink_frequency <= FATIGUE_BLINK_FREQ_THRESH,
            self.yawn_count >= FATIGUE_YAWN_THRESH,
            nod_frequency >= FATIGUE_NOD_FREQ_THRESH,
        ))
        level = "重度疲劳" if score >= 4 else "中度疲劳" if score == 3 else "轻度疲劳" if score == 2 else "正常"
        return {
            "blink_count": self.blink_count,
            "blink_frequency": round(blink_frequency, 2),
            "yawn_count": self.yawn_count,
            "nod_count": self.nod_count,
            "nod_frequency": round(nod_frequency, 2),
            "avg_perclos": round(perclos_percent / 100.0, 4),
            "speaking_ratio": round(speaking_percent / 100.0, 4),
            "fatigue_score": int(score),
            "fatigue_probability": round(score / 4.0, 4),
            "fatigue_level": level,
            "status": "fatigue" if score >= 2 else "normal",
            "face_coverage": round(self.face_frames / max(self.frames, 1), 4),
            "valid": self.face_frames > 0,
        }


def analyze_fatigue_video(video_path, landmark_path, sample_every=1):
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError("无法打开疲劳检测视频")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    analyzer = FatigueAnalyzer(landmark_path, fps=fps)
    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % max(int(sample_every), 1) == 0:
                analyzer.process_frame(frame, frame_index / fps)
            frame_index += 1
    finally:
        capture.release()
    return analyzer.result(frame_index / fps if frame_index else 0.0)
