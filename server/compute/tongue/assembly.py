"""Tongue model and capability assembly; imported only by the assembly root."""

from server.compute.tongue.service import (
    ConstitutionAssessmentApplication,
    ConstitutionReportApplication,
    TongueObservationService,
    TongueRecognitionService,
)
from server.compute.tongue.constitution.adapters.json_rules import JsonConstitutionRulesReader
from server.compute.tongue.tongue_quality.adapters.opencv import OpenCVTongueImageMetricsReader


def create_tongue_predictor():
    """EfficientNet 舌象推理器装配；重型依赖惰性导入，错误在启动时直接传播。"""
    from server.compute.tongue.tongue_recognition.adapters.efficientnet import TonguePredictor

    return TonguePredictor(device=None)


def create_tongue_recognition_service() -> TongueRecognitionService:
    """舌象识别服务；预测器槽位、锁与失败翻译都在本域 service 内。"""
    return TongueRecognitionService(create_tongue_predictor)


def create_tongue_observation_service(
    recognition: TongueRecognitionService,
) -> TongueObservationService:
    """望诊观察服务：质量度量、识别、体质与报告组合为单一窗口入口。

    ``recognition`` 必须传装配根持有的模型资源所有者（lifespan 驱动
    load/close），本装配不得另建实例。
    """
    return TongueObservationService(
        metrics_reader=OpenCVTongueImageMetricsReader(),
        recognition=recognition,
        constitution=ConstitutionAssessmentApplication(JsonConstitutionRulesReader()),
        constitution_report=ConstitutionReportApplication(JsonConstitutionRulesReader()),
    )
