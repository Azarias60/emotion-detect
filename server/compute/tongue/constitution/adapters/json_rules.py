"""JSON rules adapter for ConstitutionAssessment."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from server import settings


@lru_cache(maxsize=4)
def _read_rules(path: str) -> Mapping[str, object]:
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


class JsonConstitutionRulesReader:
    """Read a versioned rules document without leaking storage into the use case."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path is not None else (
            Path(settings.TONGUE_WEIGHTS_DIR) / "tongue" / "constitution_rules.json"
        )

    def read(self) -> Mapping[str, object]:
        return _read_rules(str(self._path))
