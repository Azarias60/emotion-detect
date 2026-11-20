"""Deterministic behavior evidence using the actual capture clock."""

import asyncio
import math

import numpy as np
import pytest

from server.analysis.video import observe_behavior
from server.capture.types import Frame, VideoWindow
from server.capture.webrtc.jpeg import OpenCvCanonicalFrameDecoder
from server.compute.fatigue.behavior_recognition import engine
from server.compute.fatigue.behavior_recognition.adapters.video import VideoBehaviorRecognizer
from server.compute.fatigue.service import FatigueObservationService
from server.compute.fatigue.types import BehaviorObservation, FaceLandmarks, FacialLandmarkExtractionResult, LandmarkPoint
from server.detection_errors import DetectionInputError
from server.tests.contract_stubs import build_jpeg_bytes


NO_FACE = FacialLandmarkExtractionResult((), "no_face")


def face(*, closed=False, mar=0.12, pitch=0.0):
    # Unit-width eyes and mouth let the real ratio functions read these values.
    points = [LandmarkPoint(0, 0)] * 68
    ear = 0.13 if closed else 0.32
    for start in (36, 42):
        points[start + 3] = LandmarkPoint(1, 0)
        points[start + 1] = points[start + 2] = LandmarkPoint(0, ear)
    points[54] = LandmarkPoint(1, 0)
    points[50] = points[52] = LandmarkPoint(0, mar)
    points[17] = LandmarkPoint(pitch, 0)
    return FacialLandmarkExtractionResult(
        (FaceLandmarks(tuple(points)),), "ok", model_source="dlib_68",
    )


@pytest.fixture(autouse=True)
def fixed_head_pose(monkeypatch):
    monkeypatch.setattr(engine, "_head_pitch", lambda shape: float(shape[17, 0]))


def analyze(samples, *, origin=87.0):
    analyzer = engine.FatigueAnalyzer()
    for timestamp, result in samples:
        analyzer.process_frame(result, origin + timestamp)
    return analyzer.behavior_result()


@pytest.mark.parametrize("step,jitter", [(0.025, 0), (0.05, 0), (0.10, 0), (0.05, 0.018)])
def test_same_actions_at_different_sampling_intervals(step, jitter):
    samples = []
    end_index = round(6.0 / step)
    for index in range(end_index + 1):
        offset = ((index % 3) - 1) * jitter if 0 < index < end_index else 0.0
        t = round(index * step + offset, 5)
        closed = 0.5 <= t < 0.72 or 1.0 <= t < 1.7
        pitch = 10.0 if 0.8 <= t < 1.4 or 2.0 <= t < 3.5 else 0.0
        if 1.5 <= t < 2.9:
            mar = 0.76
        elif 3.4 <= t < 5.6:
            mar = 0.30 + 0.16 * math.sin(2 * math.pi * 2 * (t - 3.4))
        else:
            mar = 0.12
        samples.append((t, face(closed=closed, mar=mar, pitch=pitch)))

    result = analyze(samples)
    assert (result.blink_count, result.yawn_count, result.nod_count) == (1, 1, 2)
    assert result.blink_frequency == pytest.approx(10.0)
    assert result.nod_frequency == pytest.approx(20.0)
    assert result.perclos_percent == pytest.approx(100 * 0.92 / 6, abs=1.5)
    assert result.speaking_percent == pytest.approx(40.0, abs=8.0)
    assert result.face_coverage == 1.0


@pytest.mark.parametrize("duration,expected", [(0.079, 0), (0.08, 1), (0.5, 1), (0.501, 0)])
def test_blink_confirmed_on_reopen_with_duration_bounds(duration, expected):
    closed = face(closed=True)
    samples = [(0, face()), (0.1, closed)]
    samples += [(0.1 + duration * i / 6, closed) for i in range(1, 6)]
    assert analyze(samples).blink_count == 0
    samples.append((0.1 + duration, face()))
    assert analyze(samples).blink_count == expected


def test_yawn_and_nod_require_sustained_evidence_and_recovery():
    # Both gestures start together. A short release must not rearm either one.
    samples = []
    for i in range(81):
        t = i / 20
        active = 0.3 <= t < 1.6 or 1.7 <= t < 2.7 or 3.0 <= t
        samples.append((t, face(mar=0.75, pitch=10) if active else face()))
    assert analyze(samples[:13]).nod_count == 1  # Exactly 0.30s down.
    assert analyze(samples[:26]).yawn_count == 0
    assert analyze(samples[:27]).yawn_count == 1  # Exactly 1.00s open.
    assert analyze(samples[:55]).nod_count == 1
    assert analyze(samples[:55]).yawn_count == 1
    result = analyze(samples)
    assert (result.yawn_count, result.nod_count) == (2, 2)


def test_irregular_samples_weight_eye_face_and_event_rates_by_time():
    samples = [
        (0.00, face()), (0.04, face(closed=True)), (0.16, face()),
        (0.24, NO_FACE), (0.34, NO_FACE), (0.43, NO_FACE),
        (0.50, face()), (0.60, face()), (0.71, face()), (0.80, face(pitch=10)),
        (0.91, face(pitch=10)), (1.03, face(pitch=10)), (1.10, face(pitch=10)),
    ]
    result = analyze(samples)
    face_seconds = 0.24 + 0.60
    assert result.face_coverage == pytest.approx(face_seconds / 1.10)
    assert result.perclos_percent == pytest.approx(100 * 0.12 / face_seconds)
    assert result.speaking_percent == 0.0
    assert (result.blink_count, result.nod_count) == (1, 1)
    assert result.blink_frequency == pytest.approx(60 / face_seconds)
    assert result.nod_frequency == pytest.approx(60 / face_seconds)


def test_speech_hold_expires_inside_an_irregular_interval():
    result = analyze([
        (0.0, face(mar=0.28)), (0.20, face(mar=0.45)), (0.40, face(mar=0.10)),
        (0.60, face(mar=0.10)), (0.80, face(mar=0.10)),
    ])
    assert result.speaking_percent == pytest.approx(100 * 0.55 / 0.80)


@pytest.mark.parametrize("loss", ["adaptive_gap", "long_gap", "no_face"])
def test_pending_events_and_speech_do_not_cross_missing_evidence(loss):
    samples = [
        (0.00, face(mar=0.28)), (0.05, face(mar=0.28)), (0.10, face(mar=0.28)),
        (0.15, face(mar=0.28)), (0.20, face(mar=0.50)),
        (0.25, face(closed=True, mar=0.76, pitch=10)),
    ]
    if loss == "no_face":
        samples += [(i / 20, NO_FACE) for i in range(6, 41)]
        resumes = 2.05
    else:
        resumes = 0.45 if loss == "adaptive_gap" else 5.0
    samples += [
        (resumes, face(closed=True, mar=0.76, pitch=10)),
        (resumes + 0.1, face()),
        (resumes + 0.2, face()),
    ]

    result = analyze(samples)
    assert (result.blink_count, result.yawn_count, result.nod_count) == (0, 0, 0)
    expected_face = 0.50 if loss == "no_face" else 0.45
    expected_video = 2.25 if loss == "no_face" else 0.45
    expected_closed = 0.15 if loss == "no_face" else 0.10
    expected_speech = 0.10 if loss == "no_face" else 0.05
    assert result.face_coverage == pytest.approx(expected_face / expected_video)
    assert result.perclos_percent == pytest.approx(100 * expected_closed / expected_face)
    assert result.speaking_percent == pytest.approx(100 * expected_speech / expected_face)


def test_duplicate_timestamp_keeps_the_first_sample():
    result = analyze([(0, face()), (0.10, face(closed=True)), (0.10, NO_FACE), (0.20, face())])
    assert result.blink_count == 1
    assert result.face_coverage == 1.0
    assert result.perclos_percent == pytest.approx(50.0)


@pytest.mark.parametrize("bad_time", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_capture_time_is_rejected_even_without_a_face(bad_time):
    with pytest.raises(DetectionInputError, match="有限"):
        analyze([(0, face()), (bad_time, NO_FACE)])


@pytest.mark.parametrize("bad_time", [91.95, 0.0], ids=["out-of-order", "clock-reset"])
def test_capture_time_rollback_rejects_the_window(bad_time):
    analyzer = engine.FatigueAnalyzer()
    analyzer.process_frame(face(), 92.0)
    with pytest.raises(DetectionInputError, match="回退"):
        analyzer.process_frame(face(), bad_time)


def test_empty_single_frame_and_very_short_windows():
    assert analyze([]) is None
    assert analyze([(0.0, NO_FACE), (0.1, NO_FACE)]) is None
    assert analyze([(0.0, face(closed=True))]) == BehaviorObservation(0, 0, 0, 0, 0, 0, 0, 0)
    result = analyze([(0.0, face(closed=True)), (0.001, face())])
    assert result.perclos_percent == 100.0
    assert result.face_coverage == 1.0
    assert (result.blink_count, result.blink_frequency, result.nod_frequency) == (0, 0, 0)


@pytest.mark.real_behavior_adapter
def test_bad_jpegs_keep_original_intervals_and_many_drops_break_continuity():
    jpeg = build_jpeg_bytes(size=(32, 24))
    bad_jpeg = b"undecodable jpeg"
    timestamps = (9.0, 9.05, 9.10, 9.15, 9.20, 9.25, 9.35, 9.45, 9.55, 9.65, 9.75, 9.80)
    good_times = {9.0, 9.10, 9.15, 9.20, 9.75, 9.80}
    window = VideoWindow(tuple(
        (timestamp, jpeg if timestamp in good_times else bad_jpeg, timestamp)
        for timestamp in timestamps
    ), OpenCvCanonicalFrameDecoder())
    assert [frame.timestamp for frame in window] == sorted(good_times)

    class Reader:
        def __init__(self):
            self.results = iter((face(), face(), face(closed=True), face(closed=True), face(), face()))

        def extract(self, image):
            assert isinstance(image, np.ndarray)
            return next(self.results)

    behavior = FatigueObservationService(lambda: VideoBehaviorRecognizer(Reader()))
    behavior.load()
    result = asyncio.run(observe_behavior(behavior, window))
    assert result.blink_count == 0
    assert result.face_coverage == 1.0
    assert result.perclos_percent == pytest.approx(20.0)
    empty = VideoWindow(((9.0, bad_jpeg, 9.0), (9.1, b"", 9.1)), OpenCvCanonicalFrameDecoder())
    assert asyncio.run(observe_behavior(behavior, empty)) is None


@pytest.mark.real_behavior_adapter
def test_behavior_entry_rejects_invalid_capture_frame_timeline():
    image = np.zeros((8, 8, 3), dtype=np.uint8)

    class Reader:
        def extract(self, image):
            return NO_FACE

    with pytest.raises(DetectionInputError, match="回退"):
        VideoBehaviorRecognizer(Reader()).observe_frames(((20.0, image), (0.0, image)))


@pytest.mark.real_behavior_adapter
@pytest.mark.parametrize("kind", ["behavior", "fatigue", "emotion", "health"])
def test_detection_flows_run_real_behavior_over_capture_frames(kind):
    from server.analysis.emotion.assembly import create_emotion_analysis_flow_service
    from server.analysis.emotion.reporting.types import EmotionReportInput
    from server.analysis.fatigue.reporting.flow import FatigueAnalysisFlowService
    from server.analysis.health.assembly import create_health_analysis_flow_service
    from server.analysis.health.reporting.types import HealthReportInput
    from server.compute.fatigue.service import FatigueAdviceService, FatigueScoringService
    from server.presentation.gateway.operations.observations import handle_behavior_observation
    from server.presentation.gateway.serializers import serialize_behavior_result
    from server.tests.test_gateway_operations import (
        FakeCapture, FakeExpression, FakeMeasurementReader, FakeSpeech,
        call_handler, make_ports,
    )

    class Reader:
        def __init__(self):
            self.results = iter((face(), face(closed=True), face(), NO_FACE, face()))

        def extract(self, image):
            return next(self.results)

    image = np.zeros((8, 8, 3), dtype=np.uint8)
    window = tuple(Frame(image, timestamp) for timestamp in (24.0, 24.05, 24.17, 24.25, 24.40))
    behavior = FatigueObservationService(lambda: VideoBehaviorRecognizer(Reader()))
    behavior.load()
    scoring, advice = FatigueScoringService(), FatigueAdviceService()

    if kind == "behavior":
        ports = make_ports(capture=FakeCapture(window=window), behavior=behavior)
        observed = call_handler(handle_behavior_observation, ports=ports)["data"]
    else:
        media = FakeCapture(window=window)
        reader_factory = lambda: FakeMeasurementReader()  # noqa: E731 - 每模式一个读取器

        async def run():
            if kind == "fatigue":
                return await FatigueAnalysisFlowService(
                    behavior=behavior, fatigue_scoring=scoring, fatigue_advice=advice,
                    facephys_factory=reader_factory,
                ).run(media)
            if kind == "emotion":
                measured = await create_emotion_analysis_flow_service(
                    expression=FakeExpression(), behavior=behavior,
                    facephys_factory=reader_factory, speech=FakeSpeech(),
                    fatigue_scoring=scoring, fatigue_advice=advice,
                ).measure(EmotionReportInput(), media)
            else:
                measured = await create_health_analysis_flow_service(
                    behavior=behavior, facephys_factory=reader_factory, speech=FakeSpeech(),
                    fatigue_scoring=scoring, fatigue_advice=advice,
                ).measure(HealthReportInput(), media)
            return measured

        observed = serialize_behavior_result(asyncio.run(run()).behavior)

    assert observed["blink_count"] == 1
    assert observed["blink_frequency"] == pytest.approx(240.0)
    assert observed["perclos_percent"] == pytest.approx(48.0)
    assert observed["face_coverage"] == pytest.approx(0.625)
