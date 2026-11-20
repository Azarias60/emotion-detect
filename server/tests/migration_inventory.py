"""Source inventory helpers; importing modules is never needed for scanning."""

import ast
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def production_sources():
    for directory, children, files in os.walk(ROOT / "server"):
        children[:] = sorted(
            child for child in children
            if not child.startswith(".") and child not in ("tests", "vendor", "__pycache__")
        )
        for name in sorted(files):
            if name.endswith(".py"):
                yield Path(directory) / name


def _module_exists(name):
    path = ROOT.joinpath(*name.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def imported_modules(path, *, source=None):
    name = ".".join(path.relative_to(ROOT).with_suffix("").parts)
    package = name.removesuffix(".__init__") if path.name == "__init__.py" else name.rsplit(".", 1)[0]
    for node in ast.walk(ast.parse(
        path.read_text(encoding="utf-8") if source is None else source,
    )):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = importlib.util.resolve_name(
                "." * node.level + (node.module or ""), package,
            ) if node.level else node.module or ""
            # Keep unresolved imports too: a forbidden edge must not disappear
            # merely because its target is absent from this checkout.
            if base:
                yield base
            for alias in node.names:
                target = base + "." + alias.name
                if alias.name != "*" and _module_exists(target):
                    yield target
