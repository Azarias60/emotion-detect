"""Guard public contracts, domain implementation and assembly boundaries."""

import ast
from pathlib import Path

from server.tests.migration_inventory import _module_exists, imported_modules

SERVER_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = SERVER_DIR / "tests"

COMPUTE_DOMAINS = ("expression", "fatigue", "facephys", "speech", "tongue")
ANALYSIS_DOMAINS = ("health", "emotion", "fatigue")

# 装配根一侧的测试：它们按定义消费 assembly 与装配形状。
ASSEMBLY_SIDE_TESTS = frozenset({
    "conftest.py",
    "contract_stubs.py",
    "gateway_app_factory.py",
    "runtime_fixture.py",
    "test_gateway_operations.py",
    "test_lifecycle_startup.py",
    "test_model_loading_errors.py",
    "test_engines_smoke.py",
    "test_phase_two_report_boundaries.py",
})


def _domain_root(kind, domain):
    return SERVER_DIR / kind / domain


def _production_files():
    for path in sorted(SERVER_DIR.rglob("*.py")):
        relative = path.relative_to(SERVER_DIR)
        if {"tests", "__pycache__", ".venv", "site-packages", "vendor"}.intersection(relative.parts):
            continue
        yield path


def test_compute_and_report_domains_have_public_entry_points():
    for domain in COMPUTE_DOMAINS:
        root = _domain_root("compute", domain)
        assert (root / "service.py").is_file(), domain
        assert (root / "assembly.py").is_file(), domain
        assert (root / "types" / "__init__.py").is_file(), domain
    for domain in ANALYSIS_DOMAINS:
        root = _domain_root("analysis", domain)
        assert (root / "reporting" / "flow.py").is_file(), domain
        assert (root / "reporting" / "types.py").is_file(), domain
        if domain in {"health", "emotion"}:
            assert (root / "assembly.py").is_file(), domain
            assert (root / "types.py").is_file(), domain


def test_fatigue_observation_types_do_not_reexport_report_contracts():
    from server.analysis.fatigue import types

    assert {"FatigueReportFlowPort", "FatigueReportResult"}.isdisjoint(vars(types))


def _type_paths(kind, domain):
    root = _domain_root(kind, domain)
    if kind == "compute":
        return sorted((root / "types").rglob("*.py"))
    return [path for path in (root / "types.py", root / "reporting" / "types.py") if path.is_file()]


def test_domain_types_never_import_own_implementation():
    violations = []
    for kind, domains in (("compute", COMPUTE_DOMAINS), ("analysis", ANALYSIS_DOMAINS)):
        for domain in domains:
            package = f"server.{kind}.{domain}"
            for path in _type_paths(kind, domain):
                for module in imported_modules(path):
                    if not module.startswith(package + "."):
                        continue
                    suffix = module.removeprefix(package + ".")
                    public = (suffix.split(".")[0] == "types" if kind == "compute"
                              else suffix in {"types", "reporting.types", "facts"})
                    if not public:
                        violations.append(f"{path.relative_to(SERVER_DIR)}: imports {module}")
    assert violations == []


def test_domain_value_types_have_only_invariants_and_read_only_projections():
    violations = []
    for kind, domains in (("compute", COMPUTE_DOMAINS), ("analysis", ANALYSIS_DOMAINS)):
        for domain in domains:
            for path in _type_paths(kind, domain):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
                    if any(isinstance(base, ast.Name) and base.id == "Protocol" for base in cls.bases):
                        continue
                    for node in cls.body:
                        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            continue
                        projection = any(isinstance(d, ast.Name) and d.id == "property" for d in node.decorator_list)
                        if node.name != "__post_init__" and not projection:
                            violations.append(f"{path.relative_to(SERVER_DIR)}:{node.lineno}: {cls.name}.{node.name}")
    assert violations == []


def test_service_never_imports_own_adapters():
    violations = []
    for domain in COMPUTE_DOMAINS:
        service_path = _domain_root("compute", domain) / "service.py"
        prefix = f"server.compute.{domain}."
        for module in imported_modules(service_path):
            if module.startswith(prefix):
                parts = module.removeprefix(prefix).split(".")
                if "adapters" in parts:
                    violations.append(f"compute/{domain}/service.py: imports {module}")
    assert violations == []


def test_analysis_flows_never_import_transport_or_assembly():
    paths = [_domain_root("analysis", domain) / "reporting" / "flow.py" for domain in ANALYSIS_DOMAINS]
    paths.extend(_domain_root("analysis", domain) / "observation.py" for domain in ("fatigue", "health"))
    violations = []
    for path in paths:
        for module in imported_modules(path):
            if module == "server.analysis.llm_client" or module.endswith((".reporting.text", ".assembly")):
                violations.append(f"{path.relative_to(SERVER_DIR)}: imports {module}")
    assert violations == []


def test_assemblies_define_only_functions():
    violations = []
    for kind, domains in (("compute", COMPUTE_DOMAINS), ("analysis", ("health", "emotion"))):
        for domain in domains:
            assembly = _domain_root(kind, domain) / "assembly.py"
            tree = ast.parse(assembly.read_text(encoding="utf-8"), filename=str(assembly))
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    violations.append(f"{kind}/{domain}/assembly.py: defines class {node.name}")
    assert violations == []


def _compute_domain_imports(path):
    """计算域 → 该测试导入的入口后缀集合（"service"/"types" 为公开面，其余为内部）。"""
    imports = {}
    for module in imported_modules(path):
        parts = module.split(".")
        if len(parts) > 2 and parts[0] == "server" and parts[1] == "compute":
            domain = parts[2]
            suffix = parts[3] if len(parts) > 3 else ""
            imports.setdefault(domain, set()).add(suffix)
    return imports


def _cross_domain_violations(imports_by_domain):
    """导入某计算域内部实现的测试，不得再导入任何其他计算域（含公开面）。

    跨域测试只准走各域 service/types 公开入口；需要某域内部实现的测试
    是该域的白盒测试，混入第二域（哪怕只是 types）同样是耦合。
    """
    violations = []
    for domain, suffixes in imports_by_domain.items():
        internal = suffixes - {"service", "types"}
        if not internal:
            continue
        others = sorted(set(imports_by_domain) - {domain})
        if others:
            violations.append(
                f"internal imports from {domain} while also importing {others}")
    return violations


def test_cross_domain_tests_import_only_public_entries():
    violations = []
    for path in sorted(TESTS_DIR.glob("*.py")):
        if path.name in ASSEMBLY_SIDE_TESTS:
            continue
        file_violations = _cross_domain_violations(_compute_domain_imports(path))
        if file_violations:
            violations.append(f"{path.name}: {'; '.join(file_violations)}")
    assert violations == []


def test_cross_domain_guard_intercepts_injected_violations():
    """内存注入复现的漏检形态必须被守护拒绝（A 域内部 + B 域公开 types）。"""
    violations = _cross_domain_violations({
        "facephys": {"facephys", "adapters"},
        "fatigue": {"types"},
    })
    assert violations == [
        "internal imports from facephys while also importing ['fatigue']",
    ]
    # 仅公开面的多域组合不违规
    assert _cross_domain_violations({
        "facephys": {"types"}, "fatigue": {"service", "types"},
    }) == []
    # 单域白盒（哪怕同时用公开面）不违规
    assert _cross_domain_violations({
        "facephys": {"facephys", "types"},
    }) == []


def test_server_tree_imports_all_resolve_to_real_modules():
    """迁移期 AST 守卫保留：任何 server.* 导入必须解析到真实模块（死导入即失败）。"""
    violations = []
    for path in _production_files():
        package = ".".join(path.relative_to(SERVER_DIR).with_suffix("").parts)
        package = package.removesuffix(".__init__") if path.name == "__init__.py" else package
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = ".".join(package.split(".")[: len(package.split(".")) - node.level + 1])
                    names = [f"{base}.{node.module}" if node.module else base]
                elif node.module:
                    names = [node.module]
            for name in names:
                if not name.startswith("server."):
                    continue
                if not _module_exists(name):
                    violations.append(f"{path.relative_to(SERVER_DIR)}: unresolved {name}")
    assert violations == []
