"""Boundary checks outside FaceDetection/ExpressionRecognition.

生产入口只接受内存帧工厂；旧 MP4 容器循环（打开/释放/逐帧读取）作为
基线在本文件内复刻，喂给同一 recognize_frames 核心。
"""

import numpy as np
import pytest

from server.compute.expression.expression_recognition.adapters.video import VideoExpressionCoordinator
from server.compute.expression.types import ExpressionObservation, ExpressionObservations
from server.compute.expression.types import FaceDetectionResult


def test_frame_sampling_canonical_orientation_and_aggregation_preserved():
    # 1×2 非对称像素：水平翻转可被断言区分（1 像素宽的帧翻转前后相同）
    frames = [np.asarray([[[i, i + 1, i + 2], [i + 3, i + 4, i + 5]]], dtype=np.uint8) for i in range(4)]
    samples = []

    class FaceDetection:
        def detect(self, image):
            samples.append(image.copy())
            return FaceDetectionResult((), "no_face")

    class ExpressionRecognition:
        def recognize(self, faces):
            assert faces == []
            label = f"frame-{len(samples)}"
            sample = ExpressionObservation(label, 0.75, None)
            return ExpressionObservations((sample,), ((label, 1),))

    import server.compute.expression.expression_recognition.adapters.video as coordination
    coordinator = VideoExpressionCoordinator(
        face_detection=FaceDetection(), expression_recognition=ExpressionRecognition(),
    )
    result = coordinator.recognize_frames(lambda: iter(frames), interval=2)
    assert [sample.label for sample in result.observations] == ["frame-1", "frame-2"]
    assert result.class_counts == (("frame-1", 1), ("frame-2", 1))
    assert len(samples) == 2
    # 样本直接取 capture 规范帧的原始方向转 RGB，不再做二次镜像翻转
    np.testing.assert_array_equal(samples[0], coordination.cv2.cvtColor(frames[0], coordination.cv2.COLOR_BGR2RGB))
    np.testing.assert_array_equal(samples[1], coordination.cv2.cvtColor(frames[2], coordination.cv2.COLOR_BGR2RGB))


def test_bad_interval_is_rejected_before_any_frame_is_read():
    import server.compute.expression.expression_recognition.adapters.video as coordination

    def fail():
        pytest.fail("interval must be validated before frames are read")

    coordinator = VideoExpressionCoordinator(face_detection=object(), expression_recognition=object())
    with pytest.raises(ValueError, match="抽帧间隔必须大于零"):
        coordinator.recognize_frames(fail, interval=0)


def test_execution_coordinator_does_not_import_capability_implementations():
    from pathlib import Path

    import server.compute.expression.expression_recognition.adapters.video as coordination
    source = Path(coordination.__file__).read_text()
    assert "server.compute.expression.face_detection.api" not in source
    assert "server.compute.expression.expression_recognition.application" not in source
    assert "server.compute.expression.face_detection.adapters" not in source
    assert "server.compute.expression.expression_recognition.adapters" not in source


def test_expression_frames_input_matches_the_video_file_sampling(tmp_path):
    """表情域：同一帧序列经内存窗口与 MP4 容器基线的抽样与顺序一致。

    旧生产文件入口已删除；容器循环（VideoCapture 打开/读取/释放）在
    本测试内复刻，只用于证明帧核心与旧容器链路语义等价。
    """
    import cv2

    from server.compute.expression.types import ExpressionObservations
    from server.compute.expression.expression_recognition.adapters.video import (
        VideoExpressionCoordinator,
    )

    images = []

    class Detection:
        def detect(self, rgb_image):
            images.append(rgb_image)
            from server.compute.expression.types import FaceDetectionResult
            return FaceDetectionResult((), "no_face")

    class Recognition:
        def recognize(self, face_images):
            return ExpressionObservations((), ())

    coordinator = VideoExpressionCoordinator(
        face_detection=Detection(), expression_recognition=Recognition(),
    )
    base = np.add.outer(np.arange(48), np.arange(64))[..., None].repeat(3, axis=2)
    frames = [np.clip(base * (index + 1), 0, 255).astype(np.uint8) for index in range(6)]
    path = str(tmp_path / "equivalence.mp4")
    writer = cv2.VideoWriter(
        path, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 48),
    )
    for frame in frames:
        writer.write(frame)
    writer.release()

    capture = cv2.VideoCapture(path)
    assert capture.isOpened()

    def iterate():
        while True:
            ok, frame = capture.read()
            if not ok:
                return
            yield frame

    try:
        file_result = coordinator.recognize_frames(iterate, interval=2)
    finally:
        capture.release()
    file_images = list(images)

    images.clear()
    frames_result = coordinator.recognize_frames(lambda: iter(frames), interval=2)

    assert file_result.observations == frames_result.observations == ()
    # 抽样帧数与进入检测器的顺序一致（interval=2 → 第 0/2/4 帧）
    assert len(file_images) == len(images) == 3
    for file_image, frame_image in zip(file_images, images):
        assert file_image.shape == frame_image.shape
        difference = np.abs(file_image.astype(np.int16) - frame_image.astype(np.int16))
        assert difference.mean() <= 8.0  # 仅 mp4v 有损重编码差异



def test_expression_model_lock_and_lifetime_belong_to_each_domain_instance():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from server.compute.expression.service import ExpressionVideoService

    entered, release = threading.Event(), threading.Event()
    calls = []
    expected = ExpressionObservations((ExpressionObservation("happy", 0.8, 12.0, 3),), (("happy", 1),))

    class Predictor:
        def recognize_frames(self, frames, interval):
            calls.append((list(frames()), interval))
            entered.set()
            assert release.wait(3)
            return expected

    service = ExpressionVideoService(Predictor)
    service.load()
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(service.result_frames, lambda: iter(("f0",)))
        try:
            assert entered.wait(3)
            closing = threading.Event()
            def close():
                closing.set()
                service.close()
            closed = pool.submit(close)
            assert closing.wait(3)
            assert not closed.done()
        finally:
            release.set()
        assert work.result(timeout=3) is expected
        closed.result(timeout=3)
    assert calls == [(["f0"], 10)]
    assert service.result_frames(None) is None
    with pytest.raises(RuntimeError, match="ExpressionVideoService is not loaded"):
        service.result_frames(lambda: iter(("f0",)))
    assert service._predictor is None

    # Separate application resources never share a model slot or lock.
    from server.bootstrap import assemble

    first, second = assemble(), assemble()
    assert first.expression is not second.expression
    assert first.expression._lock is not second.expression._lock
