"""Streaming fatigue behavior state machines over capture-clock observations."""

import math
from collections import deque
from statistics import median

import cv2
import numpy as np
from scipy.spatial import distance

from server.compute.fatigue.behavior_recognition import policy
from server.compute.fatigue.behavior_recognition.domain import summarize_behavior
from server.compute.fatigue.types import BehaviorObservation, FacialLandmarkExtractionResult
from server.detection_errors import DetectionInputError


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


class FatigueAnalyzer:
    def __init__(self) -> None:
        self._first_capture: float | None = None
        self._last_capture: float | None = None
        self._cadence: deque[float] = deque(maxlen=policy.CADENCE_HISTORY_INTERVALS)
        self._video_seconds = 0.0
        self._face_seconds = 0.0
        self._closed_seconds = 0.0
        self._speaking_seconds = 0.0
        self._saw_face = False
        self._blink_count = 0
        self._yawn_count = 0
        self._nod_count = 0
        self._mar_history: deque[tuple[float, float]] = deque()
        self._transitions: deque[float] = deque()
        self._reset_face_state()

    def process_frame(self, result: FacialLandmarkExtractionResult, captured_at: float) -> None:
        """Consume one decoded frame's landmarks and its original capture time."""
        if not math.isfinite(captured_at):
            raise DetectionInputError("视频采集时间戳必须为有限数值")
        if self._last_capture is not None:
            if captured_at == self._last_capture:
                return
            if captured_at < self._last_capture:
                raise DetectionInputError("视频采集时间戳回退，请重新检测")
        if self._first_capture is None:
            self._first_capture = captured_at
        now = captured_at - self._first_capture
        if not math.isfinite(now):
            raise DetectionInputError("视频采集时间跨度超出有效范围")

        previous = self._last_capture
        self._last_capture = captured_at
        if previous is not None:
            self._accumulate_interval(captured_at - previous, previous - self._first_capture)

        if result.status == "no_face":
            self._reset_face_state()
            return
        self._consume_face(result, now)

    def _accumulate_interval(self, dt: float, start: float) -> None:
        gap_limit = policy.MAX_GAP_SECONDS
        if self._cadence:
            gap_limit = min(gap_limit, max(
                policy.MIN_GAP_LIMIT_SECONDS,
                policy.GAP_CADENCE_MULTIPLIER * median(self._cadence),
            ))
        if dt > gap_limit + policy.TIME_EPSILON_SECONDS:
            self._cadence.clear()
            self._reset_face_state()
            return
        self._cadence.append(dt)
        self._video_seconds += dt
        if self._eye_closed is not None:
            self._face_seconds += dt
            if self._eye_closed:
                self._closed_seconds += dt
            if self._speaking_until is not None:
                self._speaking_seconds += min(dt, max(0.0, self._speaking_until - start))

    def _reset_face_state(self) -> None:
        self._eye_closed: bool | None = None
        self._closed_since: float | None = None
        self._yawn_since: float | None = None
        self._yawn_active = False
        self._yawn_release_since: float | None = None
        self._nod_since: float | None = None
        self._nod_armed = False
        self._neutral_since: float | None = None
        self._speaking_until: float | None = None
        self._mouth_open: bool | None = None
        self._mar_history.clear()
        self._transitions.clear()

    def _consume_face(self, result: FacialLandmarkExtractionResult, now: float) -> None:
        if result.status != "ok":
            raise ValueError(f"疲劳关键点结果违反输入契约：{result.status}/{result.reason}")
        if result.model_source != "dlib_68":
            raise ValueError(f"疲劳行为分析需要 dlib 68 点，收到 {result.model_source}")
        face = result.faces[0]
        if (
            face.coordinate_space != "pixel"
            or face.image_width <= 0
            or face.image_height <= 0
            or len(face.points) != 68
        ):
            raise ValueError("dlib 68 点结果的坐标系、图像尺寸或点数不符合疲劳分析契约")

        self._saw_face = True
        shape = np.array([[point.x, point.y] for point in face.points], dtype=np.float32)
        ear = (_eye_aspect_ratio(shape[36:42]) + _eye_aspect_ratio(shape[42:48])) / 2.0
        mar = _mouth_aspect_ratio(shape[48:68])
        self._observe_eyes(bool(ear < policy.EYE_AR_THRESHOLD), now)
        self._observe_yawn(mar, now)
        self._observe_nod(_head_pitch(shape), now)
        self._observe_speech(mar, now)

    def _observe_eyes(self, closed: bool, now: float) -> None:
        if closed and self._eye_closed is False:
            self._closed_since = now
        elif not closed and self._closed_since is not None:
            duration = now - self._closed_since
            if (policy.BLINK_MIN_CLOSED_SECONDS - policy.TIME_EPSILON_SECONDS
                    <= duration <= policy.BLINK_MAX_CLOSED_SECONDS + policy.TIME_EPSILON_SECONDS):
                self._blink_count += 1
            self._closed_since = None
        self._eye_closed = closed

    def _observe_yawn(self, mar: float, now: float) -> None:
        if self._yawn_active:
            self._yawn_release_since = _condition_start(
                self._yawn_release_since, mar <= policy.YAWN_RELEASE_MAR, now,
            )
            if _elapsed(self._yawn_release_since, now, policy.YAWN_RECOVERY_SECONDS):
                self._yawn_active = False
                self._yawn_release_since = None
            return
        self._yawn_since = _condition_start(self._yawn_since, mar >= policy.YAWN_OPEN_MAR, now)
        if _elapsed(self._yawn_since, now, policy.YAWN_MIN_OPEN_SECONDS):
            self._yawn_count += 1
            self._yawn_active = True
            self._yawn_since = None

    def _observe_nod(self, pitch: float, now: float) -> None:
        if not self._nod_armed:
            self._neutral_since = _condition_start(
                self._neutral_since, pitch <= policy.NOD_NEUTRAL_DEGREES, now,
            )
            if _elapsed(self._neutral_since, now, policy.NOD_RECOVERY_SECONDS):
                self._nod_armed = True
                self._neutral_since = None
            return
        self._nod_since = _condition_start(self._nod_since, pitch > policy.NOD_DOWN_DEGREES, now)
        if _elapsed(self._nod_since, now, policy.NOD_MIN_DOWN_SECONDS):
            self._nod_count += 1
            self._nod_armed = False
            self._nod_since = None

    def _observe_speech(self, mar: float, now: float) -> None:
        if self._mar_history:
            previous_time, previous_mar = self._mar_history[-1]
            alpha = -math.expm1(-(now - previous_time) / policy.SPEECH_SMOOTH_SECONDS)
            mar = previous_mar + alpha * (mar - previous_mar)
        self._mar_history.append((now, mar))
        cutoff = now - policy.SPEECH_WINDOW_SECONDS
        while self._mar_history[0][0] < cutoff:
            self._mar_history.popleft()
        mouth_open = (mar > policy.SPEECH_CLOSE_MAR if self._mouth_open
                      else mar >= policy.SPEECH_OPEN_MAR)
        if self._mouth_open is not None and self._mouth_open != mouth_open:
            self._transitions.append(now)
        self._mouth_open = mouth_open
        while self._transitions and self._transitions[0] < cutoff:
            self._transitions.popleft()

        if self._yawn_active or mar >= policy.YAWN_OPEN_MAR:
            self._speaking_until = None
            return
        span = now - self._mar_history[0][0]
        if span < policy.SPEECH_MIN_EVIDENCE_SECONDS - policy.TIME_EPSILON_SECONDS:
            return
        values = [value for _, value in self._mar_history]
        variation = sum(abs(current - previous) for previous, current in zip(values, values[1:]))
        dynamic_score = sum((
            max(values) - min(values) >= policy.SPEECH_MIN_RANGE,
            variation / span >= policy.SPEECH_MIN_MAR_PER_SECOND,
            len(self._transitions) >= policy.SPEECH_MIN_TRANSITIONS,
        ))
        if policy.SPEECH_MIN_MAR <= mar < policy.SPEECH_MAX_MAR and dynamic_score >= 2:
            self._speaking_until = now + policy.SPEECH_HOLD_SECONDS

    def behavior_result(self) -> BehaviorObservation | None:
        if not self._saw_face:
            return None
        return summarize_behavior(
            blink_count=self._blink_count,
            yawn_count=self._yawn_count,
            nod_count=self._nod_count,
            closed_seconds=self._closed_seconds,
            speaking_seconds=self._speaking_seconds,
            face_seconds=self._face_seconds,
            video_seconds=self._video_seconds,
        )


def _condition_start(start: float | None, active: bool, now: float) -> float | None:
    if not active:
        return None
    return now if start is None else start


def _elapsed(start: float | None, now: float, seconds: float) -> bool:
    return start is not None and now - start >= seconds - policy.TIME_EPSILON_SECONDS
