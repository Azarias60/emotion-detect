"""Tongue public entry: window-level observation over the four use cases.

``TongueObservationService`` turns a frame factory into one ``TongueResult``
fact: frame selection and the quality gate are domain policy, and the three
downstream use cases (recognition, constitution assessment and report) stay
independent results. Constitution mapping consumes the published recognition
payload inside this domain and completes rule results locally, never
delegating to the analysis domains.

``TongueRecognitionService`` is the domain resource owner: the resident
predictor slot and inference lock live here, and the failure translation from
predictor errors to the public ``Detection*`` family is public semantics — no
model import happens in this module; the assembly wires the predictor.
"""

import threading
from collections.abc import Callable

from server.compute.tongue.constitution.domain import infer_constitution_candidates
from server.compute.tongue.constitution.ports import ConstitutionRulesReader
from server.compute.tongue.constitution.report import build_constitution_report
from server.compute.tongue.types import (
    ConstitutionAssessment,
    ConstitutionReport,
    TongueRecognition,
    TongueResult,
)
from server.compute.tongue.tongue_quality.domain import assess_tongue_image_quality
from server.compute.tongue.tongue_quality.ports import TongueImageMetricsReader
from server.compute.tongue.tongue_recognition.domain import build_tongue_recognition
from server.compute.tongue.tongue_recognition.errors import (
    InvalidImageError,
    TonguePredictorError,
)
from server.compute.tongue.tongue_recognition.ports import TonguePredictor
from server.detection_errors import (
    DetectionFlowError,
    DetectionInputError,
)
from server.logutil import get_logger

logger = get_logger(__name__)


class LockedTonguePredictionReader:
    """Serializes inference against the service-owned model slot and lock."""

    def __init__(self, *, tongue_model, tongue_model_lock):
        self._tongue_model = tongue_model
        self._tongue_model_lock = tongue_model_lock

    def predict(self, image_path: str):
        with self._tongue_model_lock():
            model = self._tongue_model()
            if model is None:
                raise RuntimeError("TongueRecognitionService is not loaded")
            return model.predict(image_path)

    def predict_frame(self, frame):
        with self._tongue_model_lock():
            model = self._tongue_model()
            if model is None:
                raise RuntimeError("TongueRecognitionService is not loaded")
            return model.predict_frame(frame)


class MappedTonguePredictionReader:
    """Translate model failures into public capability errors.

    Gateway handlers only see the typed ``Detection*`` exceptions; the
    original predictor detail is logged server-side and never serialized.
    """

    def __init__(self, reader):
        self._reader = reader

    def predict(self, image_path: str):
        try:
            return self._reader.predict(image_path)
        except TonguePredictorError as error:
            logger.warning("舌象模型推理失败：%s", error)
            if isinstance(error, InvalidImageError):
                raise DetectionInputError("无法解析上传的图片") from error
            raise DetectionFlowError("舌象分析失败") from error

    def predict_frame(self, frame):
        try:
            return self._reader.predict_frame(frame)
        except TonguePredictorError as error:
            logger.warning("舌象模型推理失败：%s", error)
            if isinstance(error, InvalidImageError):
                raise DetectionInputError("无法解析上传的图片") from error
            raise DetectionFlowError("舌象分析失败") from error


class TongueRecognitionApplication:
    def __init__(self, predictor: TonguePredictor):
        self._predictor = predictor

    def result(self, image_path: str) -> TongueRecognition:
        return build_tongue_recognition(self._predictor.predict(image_path))

    def result_frame(self, image) -> TongueRecognition:
        return build_tongue_recognition(self._predictor.predict_frame(image))


class TongueRecognitionService:
    """舌象识别域资源所有者：驻留预测器与推理锁，装配根驱动 load/close。

    模型槽位 ``model`` 可被测试替身直接替换；识别入口与既有应用用例
    的失败翻译、锁定语义一致。预测器装配只发生在 assembly。
    """

    def __init__(self, predictor_factory: Callable[[], TonguePredictor]):
        self._predictor_factory = predictor_factory
        self.model = None
        self._lock = threading.Lock()
        self._application = TongueRecognitionApplication(MappedTonguePredictionReader(
            LockedTonguePredictionReader(
                tongue_model=lambda: self.model,
                tongue_model_lock=lambda: self._lock,
            ),
        ))

    def load(self) -> None:
        self.model = self._predictor_factory()

    def close(self) -> None:
        with self._lock:
            self.model = None

    def result(self, image_path: str) -> TongueRecognition:
        return self._application.result(image_path)

    def result_frame(self, image) -> TongueRecognition:
        return self._application.result_frame(image)


class ConstitutionAssessmentApplication:
    def result(self, recognition: TongueRecognition | None) -> ConstitutionAssessment | None:
        if recognition is None:
            return None
        return ConstitutionAssessment(candidates=self._infer(recognition))

    def __init__(self, rules_reader: ConstitutionRulesReader):
        self._rules_reader = rules_reader

    def _infer(self, recognition: TongueRecognition):
        return infer_constitution_candidates(
            self._rules_reader.read(),
            color_class=recognition.color.class_name,
            coated_class=recognition.coated.class_name,
            crack_class=recognition.crack.class_name,
            indent_class=recognition.indentation.class_name,
        )


class ConstitutionReportApplication:
    """Project the leading assessment candidate into its constitution report."""

    def __init__(self, rules_reader: ConstitutionRulesReader):
        self._rules_reader = rules_reader

    def result(self, assessment: ConstitutionAssessment | None) -> ConstitutionReport | None:
        if assessment is None or not assessment.candidates:
            return None
        return build_constitution_report(
            self._rules_reader.read(), assessment.candidates[0].id,
        )


class TongueObservationService:
    """望诊观察入口：帧工厂 → 域内选帧 → 质量门 → 识别 → 体质 → 报告。

    选帧策略（域内自持，一次线性扫描、常数内存）：先按质量门通过与否
    分层，同层内取清晰度（拉普拉斯方差）最高者；全部未通过时同样返回
    清晰度最高帧并如实呈现其告警，下游三个能力保持 ``None``。无可用帧
    返回 ``None``，选帧策略升级（如时序稳定度）不改变本入口契约。
    """

    def __init__(
        self,
        *,
        metrics_reader: TongueImageMetricsReader,
        recognition: TongueRecognitionService,
        constitution: ConstitutionAssessmentApplication,
        constitution_report: ConstitutionReportApplication,
    ):
        self._metrics_reader = metrics_reader
        self._recognition = recognition
        self._constitution = constitution
        self._constitution_report = constitution_report

    def result_frames(self, frames) -> TongueResult | None:
        if frames is None:
            return None
        best_key = None
        best = None  # (quality, frame)
        for frame in frames():
            metrics = self._metrics_reader.read_image(frame)
            quality = assess_tongue_image_quality(
                width=metrics.width,
                height=metrics.height,
                brightness=metrics.brightness,
                sharpness=metrics.sharpness,
            )
            key = (quality.passed, metrics.sharpness)
            if best_key is None or key > best_key:
                best_key = key
                best = (quality, frame)
        if best is None:
            return None
        quality, frame = best
        recognition = None
        assessment = None
        report = None
        if quality.passed:
            recognition = self._recognition.result_frame(frame)
            assessment = self._constitution.result(recognition)
            report = self._constitution_report.result(assessment)
        return TongueResult(
            quality=quality,
            recognition=recognition,
            constitution=assessment,
            constitution_report=report,
        )


__all__ = [
    "ConstitutionAssessmentApplication",
    "ConstitutionReportApplication",
    "LockedTonguePredictionReader",
    "MappedTonguePredictionReader",
    "TongueObservationService",
    "TongueRecognitionApplication",
    "TongueRecognitionService",
]
