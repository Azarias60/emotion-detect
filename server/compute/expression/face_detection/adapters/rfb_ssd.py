"""Outer adapter for the existing RFB-SSD face detector."""

from typing import Any

from server import settings
from server.compute.expression.types import FaceDetection, FaceDetectionResult


class RFBSSDAdapter:
    """Own the pinned RFB-SSD configuration and translate it to a typed Result."""

    def __init__(self, prb_thresh=0.85, iou_thresh=0.2, detector: Any = None):
        self.class_names = ["BACKGROUND", "face"]
        if detector is None:
            # Keep heavyweight model dependencies lazy so domain tests and
            # non-vision services can import the adapter without loading Torch.
            from server.vendor.light_detector import detector as ultra_detector
            detector = ultra_detector.Detector(
                settings.FACE_DETECTOR_WEIGHTS,
                net_type="rfb",
                input_size=[320, 320],
                class_names=self.class_names,
                priors_type="face",
                candidate_size=200,
                iou_threshold=iou_thresh,
                prob_threshold=prb_thresh,
            )
        self.detector = detector

    def detect(self, rgb_image: Any) -> FaceDetectionResult:
        if rgb_image is None:
            # 判空原在已退役的用例层；由入口适配器承接，不向模型送 None。
            return FaceDetectionResult((), "missing_image")
        boxes, labels, probabilities = self.detector.detect_image(rgb_image, vis=False)
        lengths = (len(boxes), len(labels), len(probabilities))
        if len(set(lengths)) != 1:
            raise ValueError("RFB detector boxes, labels and probabilities must have the same length")
        if lengths[0] == 0:
            return FaceDetectionResult((), "no_face")

        detections = []
        for (x1, y1, x2, y2), label, probability in zip(boxes, labels, probabilities):
            detections.append(FaceDetection(
                x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2),
                confidence=float(probability), label_id=int(label),
            ))
        result = tuple(detections)
        return FaceDetectionResult(result, "ok" if result else "no_face")
