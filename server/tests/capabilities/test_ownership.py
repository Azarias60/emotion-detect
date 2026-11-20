"""架构归属与依赖方向；扫描源码不装配整个 HTTP 应用。"""
import ast
from pathlib import Path
from server.tests.migration_inventory import imported_modules

ROOT = Path(__file__).resolve().parents[2]


def production_files():
    for path in ROOT.rglob("*.py"):
        if not {"tests", "vendor", ".venv", "__pycache__", "weights", "data"}.intersection(path.relative_to(ROOT).parts):
            yield path



def test_http_gateway_does_not_own_detection_tasks_clocks_or_acquisition_resources():
    violations = []
    for path in (ROOT / "presentation/gateway").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"create_task", "call_later", "begin_recording", "release_recording", "seal"}:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.func.attr}")
            if isinstance(node, ast.AsyncWith):
                for item in node.items:
                    if isinstance(item.context_expr, ast.Attribute) and item.context_expr.attr in {"measurement_lock", "report_lock"}:
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {item.context_expr.attr}")
    assert violations == [], "检测执行与资源使用期应归业务能力：" + ", ".join(violations)



def test_business_capabilities_do_not_depend_on_http_presentation():
    violations = []
    for path in production_files():
        relative = path.relative_to(ROOT)
        if relative.parts[0] == "presentation" or len(relative.parts) == 1:
            continue
        for target in imported_modules(path):
            if target == "server.presentation" or target.startswith("server.presentation."):
                violations.append(f"{relative}: {target}")
    assert violations == [], "协议适配应消费业务契约，业务不能依赖协议实现：" + ", ".join(violations)
