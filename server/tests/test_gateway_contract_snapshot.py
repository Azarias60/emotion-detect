"""Frozen transport contract for the capability Gateway.

Each retained observation operation is exercised through the real FastAPI app
and real capability composition; only models, hardware frames and the LLM are
replaced by the repository's existing pytest fixtures. 检测意图、报告端点与
报告观察流的形状在此一并冻结：身份来自认证边界，事实经报告提供。
The published frontend contract must contain every frozen literal, so the
documentation cannot drift silently.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from server.analysis.emotion.facts import ExpressionObservationFact
from server.analysis.facts import SpeechSegmentFact
from server.tests.runtime_fixture import (
    attach_fake_device, detach_fake_device, seed_audio, seed_frame,
)
from server.presentation.gateway import serializers as serializer_module
from server.compute.fatigue.types import BehaviorObservation

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DOC = REPO_ROOT / "docs" / "api" / "capability-gateway-contract.md"

JSON = "application/json"

# 键集从契约 DTO 派生（单一事实来源）：payload 变更只改模型，快照自动跟随。
from server.presentation.gateway import schemas as _dto
from pydantic import BaseModel
from typing import get_type_hints


def _keys(model) -> frozenset:
    if isinstance(model, type) and issubclass(model, BaseModel):
        return frozenset(model.model_fields)
    return frozenset(get_type_hints(model))


EXPRESSION_RESULT_KEYS = _keys(_dto.ExpressionResult)
EXPRESSION_OBSERVATION_KEYS = _keys(ExpressionObservationFact)
BEHAVIOR_KEYS = _keys(_dto.BehaviorResult)
SPEECH_KEYS = _keys(_dto.SpeechResult)
SPEECH_SEGMENT_KEYS = _keys(SpeechSegmentFact)


@dataclass(frozen=True)
class Operation:
    key: str
    method: str
    path: str
    content_type: str | None
    success_message: str
    data_keys: frozenset[str]
    nested_keys: dict[str, frozenset[str]]
    serializers: tuple[str, ...] = ()
    error_statuses: tuple[int, ...] = ()


OPERATIONS = (
    Operation(
        key="observations.expression",
        method="POST",
        path="/api/v1/observations/expression",
        content_type=None,
        success_message="表情识别成功",
        data_keys=EXPRESSION_RESULT_KEYS,
        nested_keys={"observations[]": EXPRESSION_OBSERVATION_KEYS},
        serializers=("serialize_expression_result",),
        error_statuses=(400, 409),
    ),
    Operation(
        key="observations.behavior",
        method="POST",
        path="/api/v1/observations/behavior",
        content_type=None,
        success_message="行为识别成功",
        data_keys=BEHAVIOR_KEYS,
        nested_keys={},
        serializers=("serialize_behavior_result",),
        error_statuses=(400, 409),
    ),
    Operation(
        key="observations.speech",
        method="POST",
        path="/api/v1/observations/speech",
        content_type=None,
        success_message="语音识别成功",
        data_keys=SPEECH_KEYS,
        nested_keys={"segments[]": SPEECH_SEGMENT_KEYS},
        serializers=("serialize_speech_result",),
        error_statuses=(400, 409),
    ),
    Operation(
        key="capture.status",
        method="GET",
        path="/api/v1/capture/status",
        content_type=None,
        success_message="采集状态",
        data_keys=frozenset({"connected", "frame_available", "audio"}),
        nested_keys={"audio": frozenset({"levels", "step_ms", "age_ms", "duration_ms"})},
        error_statuses=(),
    ),
)

# 检测意图端点：请求体、成功 data 键集与可产生的错误面。
DETECTION_INTENTS = (
    # key, method, path-suffix, request fields, data keys, auth required
    ("begin", "POST", "/api/v1/detections", ("kind",), ("reportId",), True),
    ("finish", "POST", "/api/v1/detections/{report_id}/finish", (), (), True),
    ("cancel", "POST", "/api/v1/detections/{report_id}/cancel", (), (), True),
    ("retry", "POST", "/api/v1/detections/{report_id}/retry", (), (), True),
    ("usage", "POST", "/api/v1/detections/{report_id}/usage", ("mode",), (), True),
    ("displayed", "POST", "/api/v1/detections/{report_id}/questions/displayed", ("index",), (), True),
)

REPORT_ENDPOINTS = {
    ("GET", "/api/v1/reports"): ("items",),
    ("GET", "/api/v1/reports/{report_id}"): ("id", "type", "createUserId", "createTime", "result"),
    ("DELETE", "/api/v1/reports/{report_id}"): (),
}

OBSERVATION_STREAM = ("GET", "/api/v1/observations/reports")

# 自产 JSON 500 的操作：当前无（舌象观察已退出 HTTP 面）。
DECLARED_500_OPERATIONS = frozenset()


def _operation(key):
    return {operation.key: operation for operation in OPERATIONS}[key]


def _assert_success(response, operation):
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"code", "message", "data"}
    assert body["code"] == 0
    assert body["message"] == operation.success_message
    data = body["data"]
    assert set(data) == operation.data_keys, operation.key
    for path, expected in operation.nested_keys.items():
        current = data
        for part in path.split("."):
            if part.endswith("[]"):
                items = current[part[:-2]]
                assert isinstance(items, list) and items, path
                for item in items:
                    assert set(item) == expected, path
                current = None
                break
            assert isinstance(current, dict), path
            current = current[part]
        if current is not None:
            if isinstance(current, list):
                assert current, path
                for item in current:
                    assert set(item) == expected, path
            else:
                assert set(current) == expected, path
    return data


# --- live route surface -----------------------------------------------------


def test_live_api_paths_cover_retained_operations_and_intents(client):
    live = {
        path for path in client.app.openapi()["paths"]
        if path.startswith("/api/v1/")
    }
    retained = {operation.path for operation in OPERATIONS}
    intents = {path.split("/{")[0] for _, _, path, _, _, _ in DETECTION_INTENTS}
    for _, _, path, _, _, _ in DETECTION_INTENTS:
        assert path in live, path
    for (method, path) in REPORT_ENDPOINTS:
        assert path in live, path
    assert OBSERVATION_STREAM[1] in live
    assert retained <= live
    # 已退役入口保持 404，不悄悄恢复。
    for path in (
        "/api/v1/observations/tongue", "/api/v1/assessments/emotion-questions",
        "/api/v1/reports/analyses", "/api/v1/detections/measurements",
    ):
        assert path not in live, path


def test_live_openapi_methods_match_snapshot(client):
    paths = client.app.openapi()["paths"]
    expected = {}
    for operation in OPERATIONS:
        expected[operation.path] = {operation.method.lower()}
    for _, method, path, _, _, _ in DETECTION_INTENTS:
        expected.setdefault(path, set()).add(method.lower())
    for (method, path) in REPORT_ENDPOINTS:
        expected.setdefault(path, set()).add(method.lower())
    expected.setdefault(OBSERVATION_STREAM[1], set()).add("get")
    for path, methods in expected.items():
        assert set(paths[path]) == methods, path


# --- retained observation snapshots ----------------------------------------


def test_expression_observation_snapshot(client, face_predictor):
    face_predictor(["happy", "happy", "neutral"])
    for _ in range(5):
        seed_frame()
    operation = _operation("observations.expression")

    response = client.post(operation.path)

    data = _assert_success(response, operation)
    for observation in data["observations"]:
        assert isinstance(observation["label"], str)


def test_behavior_observation_snapshot(client, fatigue_stub):
    fatigue_stub(BehaviorObservation(
        blink_count=10, blink_frequency=10.0, yawn_count=0, nod_count=0,
        nod_frequency=0.0, perclos_percent=0.0, speaking_percent=0.0,
        face_coverage=1.0,
    ))
    for _ in range(5):
        seed_frame()
    operation = _operation("observations.behavior")

    response = client.post(operation.path)

    data = _assert_success(response, operation)
    assert isinstance(data["blink_count"], int)


def test_speech_observation_snapshot(client, voice_model):
    voice_model("你好。最近有点累！")
    seed_audio(b"\x01\x00" * 16000)
    operation = _operation("observations.speech")

    response = client.post(operation.path)

    data = _assert_success(response, operation)
    assert isinstance(data["segments"], list) and data["segments"]


def test_capture_status_snapshot_offline(client):
    operation = _operation("capture.status")

    response = client.get(operation.path)

    data = _assert_success(response, operation)
    assert data["connected"] is False
    assert data["frame_available"] is False
    assert data["audio"] == {"levels": [], "step_ms": 100, "age_ms": None, "duration_ms": 0}


# --- detection intents -------------------------------------------------------


def test_detection_begin_requires_authentication(client):
    response = client.post("/api/v1/detections", json={"kind": "fatigue"})
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.xfail(
    reason="产品缺陷3：usage 意图对他人报告未校验归属即静默受理（见最终报告）",
    strict=False,
)
def test_detection_intents_verify_report_ownership(client):
    from server.tests.detection_client import auth_headers

    headers = auth_headers(client, "owner-1")
    other = auth_headers(client, "owner-2")
    attach_fake_device()
    try:
        report_id = client.post("/api/v1/detections", json={"kind": "fatigue"},
                                headers=headers).json()["data"]["reportId"]
        for path in (
            f"/api/v1/detections/{report_id}/finish",
            f"/api/v1/detections/{report_id}/cancel",
            f"/api/v1/detections/{report_id}/retry",
            f"/api/v1/detections/{report_id}/usage",
            f"/api/v1/detections/{report_id}/questions/displayed",
        ):
            response = client.post(path, headers=other,
                                   json={"mode": None} if path.endswith("/usage") else {"index": 0})
            assert response.status_code == 404, (path, response.text)
    finally:
        detach_fake_device()


def test_detection_intent_surface_and_payloads(client):
    from server.tests.detection_client import auth_headers, begin

    headers = auth_headers(client, "intent-user")
    attach_fake_device()
    try:
        report_id = begin(client, headers, "fatigue")
        # 使用条件失配取消未封存工作并移除报告。
        response = client.post(f"/api/v1/detections/{report_id}/usage",
                               json={"mode": None}, headers=headers)
        assert response.status_code == 200

        report_id = begin(client, headers, "fatigue")
        finished = client.post(f"/api/v1/detections/{report_id}/finish", headers=headers)
        assert finished.status_code == 200
        assert set(finished.json()) == {"code", "message", "data"}

        begun = client.post("/api/v1/detections", json={"kind": "fatigue"}, headers=headers)
        assert set(begun.json()["data"]) == {"reportId"}
        current = begun.json()["data"]["reportId"]
        cancelled = client.post(f"/api/v1/detections/{current}/cancel", headers=headers)
        assert cancelled.status_code == 200
        # 取消后报告被移除：再次取消返回 404。
        assert client.post(f"/api/v1/detections/{current}/cancel", headers=headers).status_code == 404
    finally:
        detach_fake_device()


def test_detection_start_refuses_kind_outside_the_three_modes(client):
    from server.tests.detection_client import auth_headers

    headers = auth_headers(client, "kind-user")
    attach_fake_device()
    try:
        response = client.post("/api/v1/detections", json={"kind": "tongue"}, headers=headers)
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)
    finally:
        detach_fake_device()


# --- reports and observation stream ------------------------------------------


def test_report_collection_detail_and_delete_shapes(client):
    from server.tests.detection_client import auth_headers, report

    headers = auth_headers(client, "reports-user")
    attach_fake_device()
    try:
        report_id = client.post("/api/v1/detections", json={"kind": "fatigue"},
                                headers=headers).json()["data"]["reportId"]
        client.post(f"/api/v1/detections/{report_id}/finish", headers=headers)

        listed = client.get("/api/v1/reports", headers=headers)
        assert listed.status_code == 200
        assert set(listed.json()["data"]) == {"items"}
        items = listed.json()["data"]["items"]
        assert items and set(items[0]) == {"id", "type", "createUserId", "createTime", "result"}

        detail = report(client, headers, report_id)
        assert detail["type"] == "fatigue"
        assert isinstance(detail["result"], dict)

        deleted = client.delete(f"/api/v1/reports/{report_id}", headers=headers)
        assert deleted.status_code == 200
        assert client.get(f"/api/v1/reports/{report_id}", headers=headers).status_code == 404
    finally:
        detach_fake_device()


def test_observation_stream_sends_current_facts_then_changes(client):
    from server.tests.detection_client import auth_headers, begin

    headers = auth_headers(client, "stream-user")
    attach_fake_device()
    try:
        with client.stream("GET", "/api/v1/observations/reports", headers=headers) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = []
            report_id = begin(client, headers, "fatigue")
            for line in response.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: "):]))
                if len(events) >= 2:
                    break
        assert events[0] == [], "订阅建立即取得当前账号事实"
        assert [row["id"] for row in events[-1]] == [report_id], "创建变化随后到达"
    finally:
        detach_fake_device()


# --- provenance and published documentation ---------------------------------


def test_snapshot_provenance_names_existing_serializers():
    for operation in OPERATIONS:
        for name in operation.serializers:
            assert name in serializer_module.__all__, (operation.key, name)


def test_declared_error_responses_equal_producible_statuses(client):
    """OpenAPI 声明的错误响应 = 实测可产生的、带官方 JSON 形状的错误状态码。"""
    schema = client.app.openapi()
    expected = {}
    for operation in OPERATIONS:
        codes = set(operation.error_statuses)
        if operation.key not in DECLARED_500_OPERATIONS:
            codes -= {500}
        expected[(operation.method, operation.path)] = codes
    for (method, path), codes in expected.items():
        responses = schema["paths"][path][method.lower()]["responses"]
        declared = {int(code) for code in responses} - {200, 422}
        assert declared == codes, (method, path, declared, codes)


def test_published_contract_document_covers_snapshot():
    assert CONTRACT_DOC.is_file(), f"missing {CONTRACT_DOC}"
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    for operation in OPERATIONS:
        marker = f"{operation.method} {operation.path}"
        assert marker in text, (operation.key, marker)
        assert f"`{operation.success_message}`" in text, operation.key
        for status in operation.error_statuses:
            assert f"`{status}`" in text, (operation.key, status)
    for _, method, path, fields, _, _ in DETECTION_INTENTS:
        assert f"{method} {path.split('/{')[0]}" in text or path in text, path
        for field in fields:
            # 请求字段以独立代码片段或 JSON 键的形式出现在契约文档中。
            assert f"`{field}`" in text or f'"{field}"' in text, (path, field)
    for path in (
        "/api/v1/observations/reports", "/api/v1/reports", "/api/v1/reports/{report_id}",
    ):
        assert path in text, path
