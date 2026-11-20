"""Frozen transport contract for the capability Gateway.

These tests pin the error mapping and Result projections used by the thin
HTTP Gateway, plus the detection-intent DTOs. They use only real capability
dataclasses; no mocks stand in for business Results. 检测意图、报告端点与
观察流的活体路由面由 ``test_gateway_contract_snapshot.py`` 冻结。
"""

import ast
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from server.compute.fatigue.types import BehaviorObservation
from server.compute.expression.types import ExpressionObservation
from server.compute.expression.types import ExpressionObservations
from server.compute.speech.types import Transcript, TranscriptSegment
from server.detection_errors import (
    DetectionBusyError,
    DetectionFlowError,
    DetectionInputError,
    DetectionUnavailableError,
)
from server.envelope import success
from server.presentation.gateway import schemas as wire_schemas
from server.presentation.gateway.serializers import (
    serialize_behavior_result,
    serialize_expression_result,
    serialize_speech_result,
)

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "presentation" / "gateway"
BANNED_IMPORTS = (
    "server.runtime",
    "server.app",
    "server.bootstrap",
    "fastapi",
    "server.capture.session",
    "server.capture.adapters",
    "server.execution",
)


# --- envelope constructor ----------------------------------------------------


def test_success_carries_public_envelope_and_is_json_serializable():
    body = success({"observations": [], "status": "ok"}, "检测成功")

    assert body == {
        "code": 0,
        "message": "检测成功",
        "data": {"observations": [], "status": "ok"},
    }
    assert json.loads(json.dumps(body)) == body
    assert success(None) == {"code": 0, "message": "成功", "data": None}


# --- capability error semantics (self-describing on the exception family) ----


def test_input_errors_carry_their_http_semantics():
    """检测输入不满足执行条件 → 400：422 只保留官方校验一种形状。"""
    for message in ("输入错误", "舌象无法识别"):
        error = DetectionInputError(message)
        assert error.http_status == 400
        assert str(error) == message


def test_busy_and_unavailable_errors_carry_dedicated_statuses():
    busy = DetectionBusyError("设备正在采集，请勿重复启动")
    assert busy.http_status == 409
    assert str(busy) == "设备正在采集，请勿重复启动"

    unavailable = DetectionUnavailableError("模型未加载")
    assert unavailable.http_status == 503
    assert str(unavailable) == "模型未加载"


def test_unclassified_flow_error_never_leaks_exception_text():
    error = DetectionFlowError("数据库口令 secret-detail")
    assert error.http_status == 500
    assert str(error) == "检测失败，请查看服务端日志"
    assert "secret-detail" not in str(error)
    assert "DetectionFlowError" not in str(error)


# --- intent DTOs -------------------------------------------------------------


def test_detection_intent_accepts_only_the_three_modes():
    adapter = TypeAdapter(wire_schemas.DetectionIntent)
    for kind in ("health", "fatigue", "emotion"):
        assert adapter.validate_python({"kind": kind}).kind == kind
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "tongue"})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        adapter.validate_python({"kind": "fatigue", "preparation": {}})


def test_usage_condition_and_question_index_shapes():
    usage = TypeAdapter(wire_schemas.UsageCondition)
    assert usage.validate_python({"mode": None}).mode is None
    assert usage.validate_python({"mode": "health"}).mode == "health"
    with pytest.raises(ValidationError):
        usage.validate_python({"mode": "tongue"})

    index = TypeAdapter(wire_schemas.QuestionIndex)
    assert index.validate_python({"index": 0}).index == 0
    with pytest.raises(ValidationError, match="extra_forbidden"):
        index.validate_python({"index": 0, "question": "x"})

    report_id = TypeAdapter(wire_schemas.DetectionReportId)
    assert report_id.validate_python({"reportId": 3}).reportId == 3


# --- Result serializers ----------------------------------------------------


def test_expression_serializer_keeps_observation_fields_and_pair_counts():
    observations = (
        ExpressionObservation(label="happy", confidence=0.9, timestamp=0.0, class_index=2),
        ExpressionObservation(label="neutral", confidence=None, timestamp=0.5, class_index=4),
        ExpressionObservation(label="happy", confidence=0.8, timestamp=1.0, class_index=2),
    )
    result = ExpressionObservations(observations, (("happy", 2), ("neutral", 1)))

    body = serialize_expression_result(result)

    assert body["class_counts"] == [["happy", 2], ["neutral", 1]]
    assert all(isinstance(pair, list) and len(pair) == 2 for pair in body["class_counts"])
    assert body["observations"] == [
        {"label": "happy", "confidence": 0.9, "timestamp": 0.0, "class_index": 2},
        {"label": "neutral", "confidence": None, "timestamp": 0.5, "class_index": 4},
        {"label": "happy", "confidence": 0.8, "timestamp": 1.0, "class_index": 2},
    ]
    assert json.loads(json.dumps(body)) == body

    empty = serialize_expression_result(ExpressionObservations((), ()))
    assert empty == {"observations": [], "class_counts": []}

    assert serialize_expression_result(None) is None


def test_behavior_serializer_keeps_unrounded_rates():
    result = BehaviorObservation(
        blink_count=7, blink_frequency=7 * 60 / 45, yawn_count=2, nod_count=1,
        nod_frequency=1 * 60 / 45, perclos_percent=18.5, speaking_percent=37.25,
        face_coverage=0.9,
    )

    body = serialize_behavior_result(result)

    assert set(body) == {
        "blink_count", "blink_frequency", "yawn_count", "nod_count",
        "nod_frequency", "perclos_percent", "speaking_percent",
        "face_coverage",
    }
    assert body["blink_frequency"] == pytest.approx(7 * 60 / 45)
    assert body["blink_frequency"] != round(7 * 60 / 45, 2)
    assert body["nod_frequency"] == pytest.approx(1 * 60 / 45)
    assert (body["perclos_percent"], body["speaking_percent"]) == (18.5, 37.25)
    assert body["face_coverage"] == 0.9
    assert json.loads(json.dumps(body)) == body


def test_speech_serializer_emits_text_segments_only():
    transcript = Transcript(
        segments=(TranscriptSegment("你好。 最近有点累！"), TranscriptSegment("心率正常吗？")),
    )
    assert serialize_speech_result(transcript) == {
        "segments": [{"text": "你好。 最近有点累！"}, {"text": "心率正常吗？"}],
    }

    assert serialize_speech_result(None) is None


# --- static hygiene --------------------------------------------------------


def _imports_from_source(source: str, path: Path):
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield path, node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                yield path, node.lineno, f"{node.module}.{alias.name}"


def _gateway_imports():
    for path in sorted(GATEWAY_DIR.rglob("*.py")):
        yield from _imports_from_source(path.read_text(encoding="utf-8"), path)


def _banned_import_violations(imports):
    return [
        (path, lineno, target)
        for path, lineno, target in imports
        for banned in BANNED_IMPORTS
        if (target == banned or target.startswith(banned + "."))
        and not (banned == "fastapi" and path == GATEWAY_DIR / "routes.py")
    ]


def test_gateway_modules_do_not_import_runtime_app_fastapi_or_legacy_wiring():
    scanned = list(_gateway_imports())
    assert scanned, "gateway package must contain importable modules"

    violations = _banned_import_violations(scanned)
    assert not violations, "; ".join(
        f"{path.relative_to(GATEWAY_DIR.parent)}:{lineno} imports {target}"
        for path, lineno, target in violations
    )


def test_static_hygiene_detects_aliased_forbidden_imports():
    source = (
        "from server import app\n"
        "from server import bootstrap\n"
        "from server.capture import session, adapters\n"
        "from fastapi import FastAPI\n"
        "import server.execution.runner\n"
        "from server.presentation.gateway import serializers\n"
    )

    violations = _banned_import_violations(
        _imports_from_source(source, Path("fake_gateway.py")),
    )

    assert [(lineno, target) for _, lineno, target in violations] == [
        (1, "server.app"),
        (2, "server.bootstrap"),
        (3, "server.capture.session"),
        (3, "server.capture.adapters"),
        (4, "fastapi.FastAPI"),
        (5, "server.execution.runner"),
    ]


def test_fastapi_exemption_is_limited_to_the_exact_routes_module():
    exact = GATEWAY_DIR / "routes.py"
    exempt = _banned_import_violations(
        _imports_from_source("from fastapi import APIRouter\n", exact),
    )
    assert exempt == []

    nested = GATEWAY_DIR / "operations" / "routes.py"
    violations = _banned_import_violations(
        _imports_from_source("from fastapi import FastAPI\n", nested),
    )
    assert [(lineno, target) for _, lineno, target in violations] == [(1, "fastapi.FastAPI")]
