"""Expression model and coordinator assembly owned by the expression domain."""

from types import SimpleNamespace

from server.compute.expression.service import ExpressionVideoService

EMOTION_CLASSES = ["angry", "disgust", "fear", "happy", "neutral", "sad", "surprise"]


def create_emotion_predictor():
    """Create the existing face-detection plus classifier coordinator."""
    import torch

    from server import settings
    from server.compute.expression.expression_recognition.adapters.clock import SystemClock
    from server.compute.expression.expression_recognition.adapters.mobilenet import (
        MobileNetV2ExpressionClassifier,
    )
    from server.compute.expression.expression_recognition.adapters.video import (
        VideoExpressionCoordinator,
    )
    from server.compute.expression.expression_recognition.application import (
        ExpressionRecognitionApplication,
    )
    from server.compute.expression.face_detection.adapters.rfb_ssd import RFBSSDAdapter

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    cfg = SimpleNamespace(
        net_type="mobilenet_v2",
        width_mult=1.0,
        input_size=[112, 112],
        rgb_mean=[0.5, 0.5, 0.5],
        rgb_std=[0.5, 0.5, 0.5],
        test_transform="val",
        num_classes=len(EMOTION_CLASSES),
        class_name=list(EMOTION_CLASSES),
        device=device,
        model_file=settings.FACE_EMOTION_WEIGHTS,
    )
    face_detection = RFBSSDAdapter()
    expression_recognition = ExpressionRecognitionApplication(
        MobileNetV2ExpressionClassifier.create(cfg), SystemClock(),
    )
    return VideoExpressionCoordinator(
        face_detection=face_detection,
        expression_recognition=expression_recognition,
    )


def create_expression_service() -> ExpressionVideoService:
    return ExpressionVideoService(create_emotion_predictor)
