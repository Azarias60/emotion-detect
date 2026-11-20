"""Gateway wiring shape: import-scan correctness, ports consumed, delegation.

迁移期清单（phase-two-boundaries.json）已降为历史文档；这里保留的两项
守护都由实时源码计算，不读任何基线 JSON：路由工厂对 ``ports`` 字段的
使用恰好覆盖 ``GatewayPorts`` 全部字段（无死字段），报告路由只委托
admission 与 flow。导入扫描器的解析能力用参数化用例直接验证。
"""

import ast
import inspect
from dataclasses import fields

import pytest

from server.presentation.gateway.operations.observations import handle_behavior_observation
from server.presentation.gateway.ports import GatewayPorts
from server.tests.migration_inventory import ROOT, imported_modules


def _tree(relative):
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _route_functions():
    return {
        node.name: node
        for node in ast.walk(_tree("server/presentation/gateway/routes.py"))
        if isinstance(node, ast.AsyncFunctionDef)
    }


@pytest.mark.parametrize("source,expected", [
    ("import server.presentation.gateway as transport", {"server.presentation.gateway"}),
    ("from server.presentation import gateway as transport", {"server.presentation", "server.presentation.gateway"}),
    ("from ....presentation.gateway import response as transport", {"server.presentation.gateway"}),
    ("from ...health.reporting import flow as sibling", {
        "server.analysis.health.reporting", "server.analysis.health.reporting.flow",
    }),
    ("from .types import EmotionReportInput as PublicInput", {
        "server.analysis.emotion.reporting.types",
    }),
    ("from server.presentation.gateway.missing_adapter import Adapter", {"server.presentation.gateway.missing_adapter"}),
        ("from server.presentation.gateway.response import JSONValue", {"server.presentation.gateway.response"}),
    ("from .missing_adapter import *", {"server.analysis.emotion.reporting.missing_adapter"}),
])
def test_import_scan_resolves_aliases_relative_exports_and_absent_targets(source, expected):
    path = ROOT / "server/analysis/emotion/reporting/flow.py"
    assert set(imported_modules(path, source=source)) == expected


def test_all_gateway_port_fields_have_exact_live_route_consumers():
    actual = {
        name: sorted({
            node.attr for node in ast.walk(function)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "ports"
        })
        for name, function in _route_functions().items()
    }
    consumed = {field for consumers in actual.values() for field in consumers}
    declared = {field.name for field in fields(GatewayPorts)}
    assert declared == consumed, "delete unconsumed fields and their composition/imports"


def test_behavior_observation_gateway_only_delegates_to_analysis():
    expected = {"admission", "behavior_observation_flow"}
    assert set(inspect.signature(handle_behavior_observation).parameters) == expected

    route = _route_functions()["observe_behavior"]
    calls = [node for node in ast.walk(route) if isinstance(node, ast.Call)]
    (delegate,) = [call for call in calls if ast.unparse(call.func) == "handle_behavior_observation"]
    assert {keyword.arg: ast.unparse(keyword.value) for keyword in delegate.keywords} == {
        name: f"ports.{name}" for name in expected
    }

    handler = ast.parse(inspect.getsource(handle_behavior_observation))
    assert [node.func.attr for node in ast.walk(handler) if (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    )] == ["acquire", "observe_behavior"]
