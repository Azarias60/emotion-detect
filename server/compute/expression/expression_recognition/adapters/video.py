"""Expression-recognition input adapter over in-memory frame factories.

It consumes face-detection Results and expression recognition Ports without
constructing either implementation; frame sampling, BGR→RGB conversion and
crop order stay at this input boundary. 文件容器循环仅存在于测试基线（测试侧自建
cv2 迭代后喂给同一核心）。
"""
from typing import Protocol

import cv2

import numpy as np

from server.compute.expression.types import ExpressionObservations, FaceDetectionResult
from server.compute.expression.expression_recognition.domain import summarize_expressions


class FaceDetector(Protocol):
    def detect(self, rgb_image) -> FaceDetectionResult: ...


class ExpressionRecognizer(Protocol):
    def recognize(self, face_images) -> ExpressionObservations: ...


class VideoExpressionCoordinator:
    """Consume capture canonical frames with the established sampling, cropping and order."""

    def __init__(self, *, face_detection: FaceDetector, expression_recognition: ExpressionRecognizer):
        self._face_detection = face_detection
        self._expression_recognition = expression_recognition

    @staticmethod
    def _crop_detected_faces(rgb_image, detection: FaceDetectionResult):
        if detection.status != "ok":
            return []

        from pybaseutils import coords_utils, image_utils

        boxes = np.asarray([
            (item.x1, item.y1, item.x2, item.y2)
            for item in detection.detections
        ], dtype=np.float32).reshape((-1, 4))
        boxes = coords_utils.extend_xyxy(boxes, scale=[1.1, 1.1])
        return image_utils.get_bboxes_crop(rgb_image, boxes)

    def task(self, image) -> ExpressionObservations:
        detection = self._face_detection.detect(image)
        faces = self._crop_detected_faces(image, detection)
        return self._expression_recognition.recognize(faces)

    def _sampled_observations(self, frames, interval: int):
        """``frames`` 为帧迭代器工厂；每次分析取一个全新迭代器。"""
        observations = []
        frame_index = 0
        for frame in frames():
            if frame_index % interval == 0:
                # capture 规范帧已含镜像校正，直接消费，不再翻转
                image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = self.task(image)
                observations.extend(result.observations)
            frame_index += 1
        return summarize_expressions(observations)

    def recognize_frames(self, frames, interval: int = 1) -> ExpressionObservations:
        """In-memory frame pass for the active MCU capture chain."""
        if interval < 1:
            raise ValueError("抽帧间隔必须大于零")
        return self._sampled_observations(frames, interval)
