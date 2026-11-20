"""No legacy aggregate route, package, or compatibility name remains.

The Gateway owns every analysis entry point. These checks fail if an old
aggregate route is registered again or if production code reintroduces the
deleted compatibility packages.
"""

from pathlib import Path

from server.bootstrap import assemble

SERVER_DIR = Path(__file__).resolve().parents[1]

DELETED_PACKAGES = (
    "server.api",
    "server.emotion_detection",
    "server.fatigue_assessment",
    "server.health_consultation",
)

FORBIDDEN_NAMES = (
    "FatigueVideoResult",
    "EmotionAnalysisResult",
    "FatigueAnalysisResult",
    "HealthAnalysisResult",
    "app.state.use_cases",
)

EXCLUDED_PARTS = {".venv", "__pycache__", "tests", "site-packages"}


def _production_sources():
    for path in sorted(SERVER_DIR.rglob("*.py")):
        relative = path.relative_to(SERVER_DIR)
        if EXCLUDED_PARTS.intersection(relative.parts):
            continue
        yield relative, path.read_text(encoding="utf-8")


def test_assembly_has_no_legacy_hardware_bundle():
    """装配只持有采集内核与各域服务，不再持有串口 MCU 单例（旧 hardware 包）。"""
    assembly = assemble()
    assert not hasattr(assembly, "hardware")


def test_production_has_no_legacy_package_reference_or_aggregate_name():
    violations = []
    for relative, source in _production_sources():
        for token in DELETED_PACKAGES + FORBIDDEN_NAMES:
            if token in source:
                violations.append(f"{relative}: {token}")
    assert violations == []
