"""Static enforcement of the result-first capability boundaries (spec §5).

Five scans pin the migrated tree:

1. inventory payload classes define no envelope status fields;
2. Gateway source never defines or serializes a ``data_status`` table;
3. capability modules import no other capability's application, presenter,
   or http module;
4. capability ports and payloads use no ``dict[str, Any]`` /
   ``Mapping[str, Any]`` contract types;
5. every inventory capability's boundary port exposes ``result`` returning the
   typed payload (or ``None``), with no legacy entry-point name.

Scans 1, 4 and 5 enumerate payloads and boundary ports by module path — the
§3 capability result inventory — so sub-capability internal results (for
example ``VideoPhysiologyMeasurement``) and internal model-facing readers stay
out of scope.
"""

import ast
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]

# --- spec §3 capability result inventory -------------------------------------

# (capability, module path under server/, payload class name).
INVENTORY_PAYLOADS = (
    ("expression recognition", "compute/expression/types/observations.py", "ExpressionObservations"),
    ("behavior recognition", "compute/fatigue/types/behavior.py", "BehaviorObservation"),
    ("speech recognition", "compute/speech/types/results.py", "Transcript"),
    ("video physiology", "compute/facephys/types/results.py", "VideoPhysiologyMeasurement"),
    ("hrv", "compute/facephys/types/results.py", "VideoHrvMeasurement"),
    ("capture heart rate", "capture/domain.py", "HeartRateWindow"),
    ("capture temperature", "capture/domain.py", "TemperatureWindow"),
    ("fatigue scoring", "compute/fatigue/types/scoring.py", "FatigueAssessment"),
    ("fatigue advice", "compute/fatigue/types/advice.py", "FatigueAdvice"),
    ("tongue quality", "compute/tongue/types/results.py", "TongueQuality"),
    ("tongue recognition", "compute/tongue/types/results.py", "TongueRecognition"),
    ("constitution assessment", "compute/tongue/types/results.py", "ConstitutionAssessment"),
    ("constitution report", "compute/tongue/types/results.py", "ConstitutionReport"),
    ("tongue observation", "compute/tongue/types/results.py", "TongueResult"),
    ("health questions", "analysis/health/types.py", "HealthQuestionSet"),
    ("emotion questions", "analysis/emotion/types.py", "EmotionQuestionSet"),
    ("emotion report", "analysis/emotion/reporting/types.py", "EmotionReportText"),
    ("health report", "analysis/health/reporting/types.py", "HealthReportText"),
)

# (capability, module path under server/, port class name, result payload,
# optional) — the boundary ports the Gateway composes: the protocols in
# owner contracts (remaining single-operation ports stay in gateway/ports.py), plus measurement readers in
# ``GatewayPorts``.
INVENTORY_PORTS = (
    ("expression recognition", "compute/expression/types/__init__.py", "ExpressionPort", "ExpressionObservations", True),
    ("behavior recognition", "compute/fatigue/types/__init__.py", "BehaviorPort", "BehaviorObservation", True),
    ("speech recognition", "compute/speech/types/__init__.py", "SpeechPort", "Transcript", True),
    ("video physiology", "compute/facephys/types/ports.py", "VideoPhysiologyReader", "VideoPhysiologyMeasurement", True),
    ("fatigue scoring", "compute/fatigue/types/__init__.py", "FatigueScoringPort", "FatigueAssessment", True),
    ("fatigue advice", "compute/fatigue/types/__init__.py", "FatigueAdvicePort", "FatigueAdvice", False),
    ("tongue observation", "compute/tongue/types/ports.py", "TonguePort", "TongueResult", True),
    ("emotion questions", "analysis/emotion/types.py", "EmotionQuestionsPort", "EmotionQuestionSet", False),
    ("emotion report", "analysis/emotion/reporting/types.py", "EmotionReportPort", "EmotionReportText", False),
    ("health report", "analysis/health/reporting/types.py", "HealthReportPort", "HealthReportText", False),
)

BANNED_PAYLOAD_FIELDS = (
    "status", "valid", "error", "used_fallback", "generation_status",
)

FORBIDDEN_CAPABILITY_MODULES = ("application", "presenter", "http")

LEGACY_ENTRY_POINTS = (
    "recognize", "recognize_frames", "estimate", "extract", "transcribe", "inspect",
    "prepare", "read", "observe", "report", "analyze",
)

CAPABILITY_DIRS = (
    "compute/expression", "compute/fatigue", "compute/facephys", "compute/speech",
    "compute/tongue", "capture", "analysis/health", "analysis/emotion",
)


def _contract_files():
    for capability in CAPABILITY_DIRS:
        directory = SERVER_DIR / capability
        assert directory.is_dir(), directory
        yield from sorted(path for path in directory.rglob("*.py")
                          if "__pycache__" not in path.parts and (
                              path.name in ("service.py", "application.py", "domain.py", "engine.py", "ports.py", "report.py")
                              or path.parent.name in ("types", "adapters")
                          ))


def test_contract_scan_covers_the_renamed_public_trio_files():
    """改名（api→service、contracts→types）后的公开面文件必须仍被契约扫描覆盖。"""
    files = {path.relative_to(SERVER_DIR).as_posix() for path in _contract_files()}
    assert "capture/types/frames.py" in files
    assert "capture/types/reads.py" in files
    assert "compute/fatigue/facial_landmarks/service.py" in files


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class_def(tree, class_name, relative):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"{relative}: missing class {class_name}")


# --- scan 1: payload classes carry no status envelope ------------------------


@pytest.mark.parametrize(
    "capability,relative,class_name", INVENTORY_PAYLOADS,
    ids=[entry[0] for entry in INVENTORY_PAYLOADS],
)
def test_inventory_payload_defines_no_status_envelope_fields(
    capability, relative, class_name,
):
    path = SERVER_DIR / relative
    node = _class_def(_parse(path), class_name, relative)
    fields = set()
    for statement in ast.walk(node):
        if isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        elif isinstance(statement, ast.Assign):
            targets = statement.targets
        else:
            continue
        fields.update(
            target.id for target in targets if isinstance(target, ast.Name)
        )
    banned = fields.intersection(BANNED_PAYLOAD_FIELDS)
    assert banned == set(), (
        f"{relative}:{class_name}: envelope fields {sorted(banned)}; a payload "
        "returns data or None, and failures raise typed exceptions"
    )


# --- scan 2: no data_status table in the Gateway ------------------------------


def test_gateway_source_has_no_data_status():
    for path in sorted((SERVER_DIR / "presentation" / "gateway").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        assert "data_status" not in source, (
            f"{path.relative_to(SERVER_DIR)}: absent data is a null result "
            "field, not a data_status table"
        )


# --- scan 3: capability isolation from transport/application modules ----------


def _import_targets(path):
    """Yield every imported module path, resolving relative imports and re-exports.

    The shared inventory helper resolves ``from ...sibling import x`` against
    the importing file's package, so a forbidden edge cannot hide behind a
    relative import or an absent target.
    """
    from server.tests.migration_inventory import imported_modules

    return imported_modules(path)


def test_capability_modules_do_not_import_another_capability_boundary_module():
    violations = []
    for capability in CAPABILITY_DIRS:
        for path in sorted((SERVER_DIR / capability).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(SERVER_DIR)
            for module in _import_targets(path):
                other = next((name for name in CAPABILITY_DIRS
                              if module.startswith("server." + name.replace("/", ".") + ".")), None)
                suffix = module.removeprefix("server." + (other or "").replace("/", ".") + ".").split(".")
                if other and other != capability and len(suffix) >= 2 and suffix[0] in FORBIDDEN_CAPABILITY_MODULES:
                    violations.append(f"{relative}: imports {module}")
    assert violations == []


# --- scan 4: no Any-valued mapping contract types -----------------------------


def _uses_any_valued_mapping(annotation):
    """True for ``dict[str, Any]`` / ``Mapping[str, Any]``, including nested."""
    for node in ast.walk(annotation):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in ("dict", "Dict", "Mapping")
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


def test_capability_ports_and_payloads_avoid_any_valued_mappings():
    violations = []
    for path in _contract_files():
        relative = path.relative_to(SERVER_DIR)
        for lineno, annotation in _signature_annotations(_parse(path)):
            if _uses_any_valued_mapping(annotation):
                violations.append(
                    f"{relative}:{lineno}: {ast.unparse(annotation)}"
                )
    assert violations == []


# --- scan 5: boundary ports expose result -------------------------------------


@pytest.mark.parametrize(
    "capability,relative,class_name,payload,optional", INVENTORY_PORTS,
    ids=[entry[0] for entry in INVENTORY_PORTS],
)
def test_inventory_boundary_port_exposes_result(
    capability, relative, class_name, payload, optional,
):
    path = SERVER_DIR / relative
    node = _class_def(_parse(path), class_name, relative)
    methods = [
        statement for statement in node.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    names = {method.name for method in methods}
    assert names & {"result", "result_frames"}, (
        f"{relative}:{class_name}: boundary must expose result/result_frames"
    )
    legacy = names.intersection(LEGACY_ENTRY_POINTS)
    assert legacy == set(), (
        f"{relative}:{class_name}: legacy entry points {sorted(legacy)} remain"
    )
    result_method = next(
        method for method in methods if method.name in ("result", "result_frames")
    )
    expected = f"{payload} | None" if optional else payload
    assert ast.unparse(result_method.returns) == expected, (
        f"{relative}:{class_name}.result must return {expected}"
    )
