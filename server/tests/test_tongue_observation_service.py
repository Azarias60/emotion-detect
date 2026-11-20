# -*-coding: utf-8 -*-
"""望诊观察服务：域内选帧策略、质量门与四能力组合的单元契约。

选帧是舌象域的自持策略：先按质量门通过与否分层，同层内取清晰度最高者；
全部未通过时如实返回最优帧告警、下游能力为 None。帧以原对象直达识别
模型，无复制、无落盘。
"""
import ast
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import pytest

from server.compute.tongue.service import TongueObservationService
from server.compute.tongue.tongue_quality.adapters.opencv import OpenCVTongueImageMetricsReader
from server.compute.tongue.tongue_quality.ports import TongueImageMetrics
from server.compute.tongue.types import (
    ConstitutionAssessment,
    ConstitutionCandidate,
    ConstitutionReport,
    TongueDimensionResult,
    TongueQuality,
    TongueRecognition,
    TongueResult,
)


RECOGNITION = TongueRecognition(
    crack=TongueDimensionResult("normal", 0.81),
    coated=TongueDimensionResult("white", 0.65),
    color=TongueDimensionResult("red", 0.72),
    indentation=TongueDimensionResult("normal", 0.55),
)
ASSESSMENT = ConstitutionAssessment((
    ConstitutionCandidate(1, "平和质", "Balanced", 0.9, "normal"),
))
REPORT = ConstitutionReport(
    constitution_id=1, constitution_name="平和质", constitution_english="Balanced",
    category="正常体质", one_line="阴阳气血调和", body_signals=(), tongue="淡红舌",
    mind_summary="情绪稳定", mind_highlights=(), disease_risk=(),
    environment_note="顺应自然", advice={},
)


def _metrics(brightness=120.0, sharpness=45.0, width=320, height=240):
    return TongueImageMetrics(
        width=width, height=height, brightness=brightness, sharpness=sharpness,
    )


class StubMetricsReader:
    """按帧对象身份返回受控度量的替身。"""

    def __init__(self, metrics_by_frame):
        self._metrics = metrics_by_frame

    def read_image(self, image):
        return self._metrics[image]


class StubRecognition:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def result_frame(self, image):
        self.calls.append(image)
        if self.error is not None:
            raise self.error
        return RECOGNITION


class StubConstitution:
    def __init__(self):
        self.calls = []

    def result(self, recognition):
        self.calls.append(recognition)
        return ASSESSMENT


class StubReport:
    def __init__(self):
        self.calls = []

    def result(self, assessment):
        self.calls.append(assessment)
        return REPORT


def _service(metrics_by_frame, recognition=None):
    return TongueObservationService(
        metrics_reader=StubMetricsReader(metrics_by_frame),
        recognition=recognition if recognition is not None else StubRecognition(),
        constitution=StubConstitution(),
        constitution_report=StubReport(),
    )


def test_none_factory_and_empty_window_yield_no_result():
    assert TongueObservationService(
        metrics_reader=StubMetricsReader({}),
        recognition=StubRecognition(),
        constitution=StubConstitution(),
        constitution_report=StubReport(),
    ).result_frames(None) is None

    service = _service({})
    assert service.result_frames(lambda: iter(())) is None


def test_passing_tier_ranks_by_sharpness_and_winner_reaches_the_model():
    """先质量门分层、再比清晰度：未通过帧清晰度再高也不入选。"""
    dim_pass = object()      # 通过层，清晰度低
    sharp_pass = object()    # 通过层，清晰度最高 → 胜出帧
    sharp_fail = object()    # 未通过层，清晰度全场最高但被分层排除
    service = _service({
        dim_pass: _metrics(sharpness=50.0),
        sharp_pass: _metrics(sharpness=90.0),
        sharp_fail: _metrics(brightness=10.0, sharpness=200.0),  # 偏暗 → 未通过
    })
    recognition = service._recognition

    result = service.result_frames(
        lambda: iter((dim_pass, sharp_fail, sharp_pass)))

    assert isinstance(result, TongueResult)
    assert recognition.calls == [sharp_pass]  # 原对象直达模型
    assert result.quality.passed is True
    assert result.recognition is RECOGNITION
    assert result.constitution is ASSESSMENT
    assert result.constitution_report is REPORT


def test_all_failing_frames_report_best_effort_quality_with_nulls():
    dark = object()       # 偏暗
    dark_blurry = object()  # 偏暗且更糊
    service = _service({
        dark: _metrics(brightness=10.0, sharpness=60.0),
        dark_blurry: _metrics(brightness=10.0, sharpness=5.0),
    })
    recognition = service._recognition

    result = service.result_frames(lambda: iter((dark_blurry, dark)))

    assert result.quality.passed is False
    assert result.quality.sharpness == 60.0  # 未通过层内仍取清晰度最高者
    assert "画面偏暗，请增加正面光线" in result.quality.warnings
    assert result.recognition is None
    assert result.constitution is None
    assert result.constitution_report is None
    assert recognition.calls == []  # 质量门未过：模型根本不被调用


def test_quality_metrics_round_only_on_presentation_values():
    """阈值比较用未取整度量，展示值才四舍五入（沿用既有语义）。"""
    frame = object()
    service = _service({frame: _metrics(brightness=44.999, sharpness=34.999)})

    result = service.result_frames(lambda: iter((frame,)))

    assert result.quality.passed is False
    assert (result.quality.brightness, result.quality.sharpness) == (45.0, 35.0)
    assert result.quality.warnings == (
        "画面偏暗，请增加正面光线",
        "画面较模糊，请保持舌头和摄像头稳定",
    )


def test_recognition_failures_propagate_unchanged():
    frame = object()
    service = _service(
        {frame: _metrics()},
        recognition=StubRecognition(error=RuntimeError("tongue inference failed")),
    )

    with pytest.raises(RuntimeError, match="tongue inference failed"):
        service.result_frames(lambda: iter((frame,)))


def test_observation_service_has_no_concrete_adapter_or_opencv_import():
    import server.compute.tongue.service as application
    import server.compute.tongue.tongue_quality.ports as ports
    for module in (application, ports):
        source = Path(module.__file__).read_text(encoding="utf-8")
        imports = [node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))]
        names = [alias.name for node in imports for alias in node.names] + [
            node.module or "" for node in imports if isinstance(node, ast.ImportFrom)
        ]
        assert not any(name.startswith(("cv2", "numpy", "server.compute.tongue.tongue_quality.adapters", "server.runtime")) for name in names)


def test_opencv_reader_projects_existing_metrics(tmp_path):
    """真实适配器：灰度均值 = 亮度、拉普拉斯方差 = 清晰度。"""
    image = np.arange(300 * 240 * 3, dtype=np.uint8).reshape(300, 240, 3)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    metrics = OpenCVTongueImageMetricsReader().read_image(image)
    assert metrics.brightness == float(np.mean(gray))
    assert metrics.sharpness == float(cv2.Laplacian(gray, cv2.CV_64F).var())
    assert (metrics.width, metrics.height) == (240, 300)


def test_quality_fact_is_a_frozen_value_object():
    quality = TongueQuality(
        passed=True, warnings=(), width=320, height=240,
        brightness=120.0, sharpness=45.0,
    )
    assert asdict(quality)["passed"] is True
    with pytest.raises(Exception):
        quality.passed = False
