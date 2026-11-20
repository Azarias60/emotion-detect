"""Guard settled capability boundaries: final isolation rules (RFC-5).

§2/§10 最终依赖规则：五个感知计算域互不导入（包括结果类型所在的 types），
也不导入采集、综合分析、presentation 或 support；capture 与两个 support
模块不反向依赖计算域或综合分析。分析综合域按 RFC-5 编排感知域：允许导入采集与计算域的公开契约，
但彼此不导入，也不导入 presentation 或 support。组合层的公开入口约束由
``test_architecture_boundaries`` 的固定入口扫描执行。
"""

from pathlib import Path

import pytest

from server.tests.migration_inventory import imported_modules

SERVER_DIR = Path(__file__).resolve().parents[1]

# RFC-5：五个纯感知计算域（compute/cognition 已退役为 analysis 业务大脑，POS/KDE 已下线）。
PERCEPTION_DOMAINS = (
    "compute/expression", "compute/fatigue", "compute/facephys", "compute/speech",
    "compute/tongue",
)
ANALYSIS_DOMAINS = ("analysis/health", "analysis/emotion", "analysis/fatigue")
CAPABILITIES = (
    *PERCEPTION_DOMAINS, "capture",
    "support/identity", "support/reporting",
)
ALL_DOMAIN_PREFIXES = tuple(
    f"server.{owner.replace('/', '.')}" for owner in (*CAPABILITIES, *ANALYSIS_DOMAINS)
)


def _banned_prefixes(owner):
    module = f"server.{owner.replace('/', '.')}"
    if owner in ANALYSIS_DOMAINS:
        # 分析域编排感知能力：compute/capture 公开契约可用；同级分析域、
        # presentation 与 support 禁入（公开入口由固定入口扫描约束）。
        return tuple(
            f"server.{other.replace('/', '.')}" for other in ANALYSIS_DOMAINS
            if other != owner
        ) + ("server.presentation", "server.support")
    if owner == "capture":
        return ("server.compute", "server.analysis", "server.presentation", "server.support")
    if owner.startswith("compute/"):
        # 计算域不导入任何其他业务模块（含其余域的 types）。
        return tuple(
            prefix for prefix in ALL_DOMAIN_PREFIXES if prefix != module
        ) + ("server.analysis", "server.presentation")
    return ("server.compute", "server.capture", "server.analysis", "server.presentation")


def _forbidden_imports(owner, path, *, source=None):
    banned = _banned_prefixes(owner)
    return {
        target for target in imported_modules(path, source=source)
        if any(target == prefix or target.startswith(prefix + ".") for prefix in banned)
    }


@pytest.mark.parametrize(
    "owner,path",
    [(owner, path) for owner in (*CAPABILITIES, *ANALYSIS_DOMAINS)
     for path in (SERVER_DIR / owner).rglob("*.py")
     if "__pycache__" not in path.parts],
)
def test_capability_files_import_no_other_business_module(owner, path):
    violations = _forbidden_imports(owner, path)
    assert not violations, f"{path.relative_to(SERVER_DIR)}: {sorted(violations)}"


@pytest.mark.parametrize("source,target", [
    ("import server.capture", "server.capture"),
    ("from server import capture as device", "server.capture"),
    ("from server.capture.types import Frame", "server.capture.types"),
    ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import server.capture.types as capture",
     "server.capture.types"),
    ("from ...capture.types import Frame", "server.capture.types"),
])
def test_fatigue_compute_guard_rejects_capture_even_in_type_only_imports(source, target):
    owner = "compute/fatigue"
    assert target in _forbidden_imports(owner, SERVER_DIR / owner / "service.py", source=source)


def test_capture_guard_rejects_the_compute_package_itself():
    owner = "capture"
    assert _forbidden_imports(
        owner, SERVER_DIR / owner / "domain.py", source="from server import compute",
    ) == {"server.compute"}


# Do not reintroduce import aliases for deliberately removed implementation paths.
RETIRED_IMPLEMENTATION_PATHS = (
    "compute/measurement",
    "analysis/emotion/llm.py",
    "analysis/emotion/flow.py",
    "analysis/emotion/service.py",
    "analysis/emotion/reporting.py",
    "analysis/health/flow.py",
    "analysis/health/service.py",
    "analysis/health/reporting.py",
    "analysis/fatigue/flow.py",
    "analysis/health/llm.py",
    "analysis/health/errors.py",
    "analysis/health/adapters/models.py",
    "analysis/health/adapters/tongue.py",
    "analysis/health/adapters/__init__.py",
    "engines/health_models.py",
)


def test_retired_implementation_shims_are_absent():
    for relpath in RETIRED_IMPLEMENTATION_PATHS:
        assert not (SERVER_DIR / relpath).exists(), relpath
