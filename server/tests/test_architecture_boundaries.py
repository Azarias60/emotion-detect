"""Static architecture checks for the Gateway and deleted aggregate entry points."""

import ast
from pathlib import Path

from server.tests.migration_inventory import imported_modules
from server.tests.test_typed_result_boundaries import BUSINESS_MODULES

SERVER_DIR = Path(__file__).resolve().parents[1]
GATEWAY_DIR = SERVER_DIR / "presentation" / "gateway"

DELETED_PACKAGES = (
    "api",
    "emotion_detection",
    "fatigue_assessment",
    "health_consultation",
    "compute/cognition",
)

# Business capability packages. Infrastructure and transport verticals
# (gateway, reporting, identity) are scanned separately or not at all here.
CAPABILITY_DIRS = (
    "compute/expression",
    "compute/fatigue",
    "compute/facephys",
    "compute/speech",
    "compute/tongue",
    "capture",
)

CAPABILITY_BUSINESS_FILES = (
    "application.py",
    "domain.py",
    "engine.py",
    "ports.py",
    "report.py",
)

FORBIDDEN_CAPABILITY_MODULES = (
    "server.app",
    "server.execution",
    "server.presentation.gateway",
    "server.runtime",
)

FORBIDDEN_RUNTIME_ACCESS = ("use_runtime", "get_runtime", "with_runtime")

BANNED_FACADES = (
    "analysis_services",
    "runtime.services",
    "AnalysisApplication",
    "EmotionApplication",
    "FatigueApplication",
    "HealthApplication",
)

# §8 target tree: business modules and their public entry surfaces. Cross-module
# business imports may target only the entry modules of the target tree: the
# compute domains expose service/types. Analysis exposes domain facts, observation
# types and report contracts. Assembly stays assembly-root-only.
BUSINESS_ENTRY_POINTS = (
    ("server.analysis.emotion", "analysis/emotion"),
    ("server.analysis.fatigue", "analysis/fatigue"),
    ("server.analysis.health", "analysis/health"),
    ("server.capture", "capture"),
    ("server.compute.expression", "compute/expression"),
    ("server.compute.fatigue", "compute/fatigue"),
    ("server.compute.facephys", "compute/facephys"),
    ("server.compute.speech", "compute/speech"),
    ("server.compute.tongue", "compute/tongue"),
    ("server.presentation.gateway", "presentation/gateway"),
    ("server.presentation.signaling", "presentation/signaling"),
    ("server.presentation.preview", "presentation/preview.py"),
    ("server.support.identity", "support/identity"),
    ("server.support.reporting", "support/reporting"),
)

ASSEMBLY_ROOTS = ("app.py", "bootstrap.py")

def _source(path):
    return path.read_text(encoding="utf-8")


def _parse(path):
    return ast.parse(_source(path), filename=str(path))


def _import_targets(path):
    """Yield every imported module path, absolute or relative, including re-exports.

    Relative imports are resolved against the importing file's package, so a
    forbidden edge cannot hide behind ``from ...sibling import x``; ``from
    package import symbol`` targets survive only when they name real modules.
    """
    return imported_modules(path)


def _matches(module, target):
    return module == target or module.startswith(target + ".")


def _owner_module(relative: str):
    for prefix, directory in BUSINESS_ENTRY_POINTS:
        target = directory.removesuffix(".py")
        if relative == directory or relative.startswith(target + "/"):
            return prefix
    return None


def _target_module(imported: str):
    for prefix, _ in BUSINESS_ENTRY_POINTS:
        if imported == prefix or imported.startswith(prefix + "."):
            return prefix
    return None


def _capability_paths():
    for capability in CAPABILITY_DIRS:
        for path in sorted((SERVER_DIR / capability).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield capability, path


def _business_modules():
    """Capability Result/Port modules plus the Gateway's business layer.

    Transport-only Pydantic models (for example ``server/reporting/http.py``)
    are deliberately outside this scan; no business module may opt out.
    """
    for relative in BUSINESS_MODULES:
        assert (SERVER_DIR / relative).is_file(), relative
    modules = {SERVER_DIR / relative for relative in BUSINESS_MODULES}
    for capability in CAPABILITY_DIRS:
        directory = SERVER_DIR / capability
        assert directory.is_dir(), capability
        assert any(
            path.suffix == ".py" for path in directory.rglob("*")
            if "__pycache__" not in path.parts
        ), f"{capability}: scanned directory holds no source"
        modules.update(path for path in directory.rglob("*.py")
                       if path.name in CAPABILITY_BUSINESS_FILES
                       or "contracts" in path.parts or "types" in path.parts)
    modules.update(GATEWAY_DIR.rglob("*.py"))
    return sorted(
        path for path in modules
        if path.is_file() and "__pycache__" not in path.parts
    )


def test_gateway_does_not_import_runtime_or_outer_composition():
    forbidden = (
        "server.app",
        "server.runtime",
        "server.bootstrap",
        "server.capture.adapters",
    )
    for path in sorted(GATEWAY_DIR.rglob("*.py")):
        for module in _import_targets(path):
            assert not any(
                module == target or module.startswith(target + ".")
                for target in forbidden
            ), f"{path.relative_to(SERVER_DIR)}: {module}"


def test_no_aggregate_facade_references_remain_in_production():
    for path in sorted(SERVER_DIR.rglob("*.py")):
        relative = path.relative_to(SERVER_DIR)
        if {"tests", "__pycache__", ".venv", "site-packages"}.intersection(relative.parts):
            continue
        source = path.read_text(encoding="utf-8")
        for token in BANNED_FACADES:
            assert token not in source, f"{relative}: {token}"


def test_runtime_wiring_has_no_removed_aggregate_entrypoints():
    checked = (
        SERVER_DIR / "app.py",
        SERVER_DIR / "bootstrap.py",
        SERVER_DIR / "compute/fatigue/behavior_recognition/adapters/video.py",
    )
    banned = (
        "create_components",
        "app.state.use_cases",
        "emotion_runner",
        "fatigue_runner",
        "health_runner",
        "tongue_runner",
        "captured_tongue_runner",
    )
    for path in checked:
        source = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in source, f"{path.relative_to(SERVER_DIR)}: {token}"


def test_deleted_aggregate_packages_are_absent():
    for package in DELETED_PACKAGES:
        assert not (SERVER_DIR / package).exists(), package


# --- capability runtime isolation ------------------------------------------


def test_capture_l2_domain_and_contracts_stay_codec_free():
    """采集领域层与契约层必须 100% 编解码器无关（RFC-8 依赖倒置守卫）。

    ``capture/domain.py`` 与 ``capture/types/`` 严禁 import 任何
    ``server.capture.webrtc.*`` 模块，也严禁引入 ``cv2``：规范帧解码器经
    ``CanonicalFrameDecoder`` 协议注入，具体实现留在基础设施层。
    """
    guarded = [SERVER_DIR / "capture" / "domain.py"]
    guarded += sorted((SERVER_DIR / "capture" / "types").glob("*.py"))
    assert guarded, "capture 契约文件缺失"
    violations = []
    for path in guarded:
        relative = path.relative_to(SERVER_DIR)
        for module in _import_targets(path):
            if module == "cv2" or _matches(module, "server.capture.webrtc"):
                violations.append(f"{relative}: imports {module}")
    assert violations == []


def test_capability_modules_do_not_import_runtime_app_or_gateway():
    violations = []
    for capability, path in _capability_paths():
        relative = path.relative_to(SERVER_DIR)
        for module in _import_targets(path):
            for forbidden in FORBIDDEN_CAPABILITY_MODULES:
                if _matches(module, forbidden):
                    violations.append(f"{relative}: imports {module}")
        source = _source(path)
        for token in FORBIDDEN_RUNTIME_ACCESS:
            if token in source:
                violations.append(f"{relative}: uses {token}")
    assert violations == []


# Cross-capability application/presenter/http imports are banned by
# test_result_view_boundaries.py, which tightens the former transport-only
# scan to include capability application modules.


# --- business typing --------------------------------------------------------


def _uses_any_dict(annotation):
    """True for ``dict[str, Any]`` / ``Dict[str, Any]`` annotations, including unions."""
    for node in ast.walk(annotation):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in ("dict", "Dict")
            and isinstance(node.slice, ast.Tuple)
            and len(node.slice.elts) == 2
            and isinstance(node.slice.elts[0], ast.Name)
            and node.slice.elts[0].id == "str"
            and isinstance(node.slice.elts[1], ast.Name)
            and node.slice.elts[1].id == "Any"
        ):
            return True
    return False


def _signature_annotations(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = (
                node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            )
            for argument in arguments:
                if argument.annotation is not None:
                    yield node.lineno, argument.annotation
            if node.returns is not None:
                yield node.lineno, node.returns
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            yield node.lineno, node.annotation


def test_business_results_and_ports_do_not_use_dict_str_any():
    violations = []
    for path in _business_modules():
        relative = path.relative_to(SERVER_DIR)
        for lineno, annotation in _signature_annotations(_parse(path)):
            if _uses_any_dict(annotation):
                violations.append(f"{relative}:{lineno}: {ast.unparse(annotation)}")
    assert violations == []


# --- final dependency rules (phase five) -------------------------------------


def _production_files():
    for path in sorted(SERVER_DIR.rglob("*.py")):
        relative = path.relative_to(SERVER_DIR)
        if {"tests", "__pycache__", ".venv", "site-packages", "vendor"}.intersection(
            relative.parts,
        ):
            continue
        yield path


def test_analysis_report_flows_do_not_import_each_other():
    """报告流程可复用共享计算，流程之间保持独立。"""
    # 三种已确认报告流程独立；不以目录穷举阻止新的完整业务能力归位。
    entries = {"emotion", "fatigue", "health"}
    for flow in sorted(entries):
        directory = SERVER_DIR / "analysis" / flow
        assert any(
            path.suffix == ".py" for path in directory.rglob("*")
            if "__pycache__" not in path.parts
        ), flow
        other_flows = {f"server.analysis.{other}" for other in entries - {flow}}
        for path in directory.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for module in _import_targets(path):
                assert not any(_matches(module, other) for other in other_flows), (
                    f"{path.relative_to(SERVER_DIR)}: {module}"
                )


def test_cross_module_business_imports_use_only_public_entries():
    """§8 固定入口：跨模块业务导入只允许目标树的入口模块
    （计算域 service/types，analysis 与 capture 的 api/contracts）；装配根除外。"""
    violations = []
    for path in _production_files():
        relative = path.relative_to(SERVER_DIR).as_posix()
        if relative in ASSEMBLY_ROOTS:
            continue
        owner = _owner_module(relative)
        for imported in _import_targets(path):
            target = _target_module(imported)
            if target is None or target == owner:
                continue
            suffix = imported.removeprefix(target + ".")
            if target.startswith("server.analysis."):
                public = suffix in {"facts", "types", "reporting.types"}
            else:
                public = suffix.split(".")[0] in {"service", "types", "api", "contracts"}
            if not public:
                violations.append(f"{relative}: imports {imported}")
    assert violations == []


def test_module_assemblies_are_imported_only_by_the_assembly_root():
    """§8：创建与关闭资源的 assembly 只允许 bootstrap 导入。"""
    violations = []
    for path in _production_files():
        relative = path.relative_to(SERVER_DIR).as_posix()
        if relative == "bootstrap.py":
            continue
        for imported in _import_targets(path):
            target = _target_module(imported)
            if target is None:
                continue
            if _matches(imported, f"{target}.assembly") or _matches(imported, f"{target}.factory"):
                violations.append(f"{relative}: imports {imported}")
    assert violations == []


def _reexport_violations(namespace, imported):
    """包根 ``__init__`` 的重导出违规：内部模块路径不得经包根公开。"""
    violations = []
    for module in imported:
        if _matches(module, namespace):
            suffix = module.removeprefix(namespace + ".").split(".")[0]
            if suffix not in ("service", "types", "api", "contracts"):
                violations.append(f"re-exports {module}")
    return violations


def test_package_roots_do_not_reexport_module_internals():
    """§8：包根 __init__ 不得通过重导出内部实现绕过入口约束。"""
    roots = [SERVER_DIR / directory for _, directory in BUSINESS_ENTRY_POINTS
             if not directory.endswith(".py")]
    roots += [SERVER_DIR / name for name in ("analysis", "compute", "presentation", "support")]
    violations = []
    for root in roots:
        init = root / "__init__.py"
        if not init.is_file():
            continue
        relative_parts = init.relative_to(SERVER_DIR).with_suffix("").parts
        # __init__.py 的包名即目录路径，不能把 __init__ 组件算进命名空间。
        relative_parts = tuple(part for part in relative_parts if part != "__init__")
        namespace = "server." + ".".join(relative_parts)
        for violation in _reexport_violations(namespace, _import_targets(init)):
            violations.append(f"{init.relative_to(SERVER_DIR)}: {violation}")
    assert violations == []


def test_package_root_guard_intercepts_injected_reexports():
    """内存注入复现的漏检形态必须被守护拒绝（经包根重导出域内部模块）。"""
    assert _reexport_violations(
        "server.compute", ["server.compute.facephys.tracker"],
    ) == ["re-exports server.compute.facephys.tracker"]
    assert _reexport_violations(
        "server.compute.facephys", ["server.compute.facephys.engine"],
    ) == ["re-exports server.compute.facephys.engine"]
    # 公开入口（service/types）的重导出不违规
    assert _reexport_violations(
        "server.compute.facephys", ["server.compute.facephys.types"],
    ) == []
    # 无关命名空间的导入不违规
    assert _reexport_violations(
        "server.compute", ["server.analysis.emotion.types"],
    ) == []


def test_frozen_hardware_readings_do_not_escape_capture():
    """Frozen sensor storage may remain in capture, never become a gateway/analysis feature."""
    names = {"HeartRateWindow", "TemperatureWindow", "HardwareHeartRateSource",
             "heart_rate_window", "temperature_window"}
    for folder in (SERVER_DIR / "analysis", SERVER_DIR / "compute", GATEWAY_DIR):
        for path in folder.rglob("*.py"):
            tree = _parse(path)
            exposed = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            exposed |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            exposed |= {node.name for node in ast.walk(tree) if isinstance(node, ast.alias)}
            assert not exposed & names, f"{path}: frozen hardware projection {exposed & names}"
