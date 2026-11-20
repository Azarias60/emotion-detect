"""Public tongue result payloads owned by the tongue compute domain.

One observation yields one ``TongueResult`` fact bundling the four payloads;
each member stays an independent typed payload and is serialized (and echoed
by the health report) on its own. The model's softmax probability is
classification confidence, not a clinical certainty or an overall quality
score. Internal policy functions remain in the owning sub-capability modules
and import these payloads from here.
"""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class TongueQuality:
    passed: bool
    warnings: tuple[str, ...]
    width: int
    height: int
    brightness: float
    sharpness: float


@dataclass(frozen=True)
class TongueDimensionResult:
    class_name: str
    probability: float


@dataclass(frozen=True)
class TongueRecognition:
    crack: TongueDimensionResult
    coated: TongueDimensionResult
    color: TongueDimensionResult
    indentation: TongueDimensionResult


@dataclass(frozen=True)
class ConstitutionCandidate:
    id: int
    name: str
    english: str
    weight: float
    category: str


@dataclass(frozen=True)
class ConstitutionAssessment:
    candidates: tuple[ConstitutionCandidate, ...]


@dataclass(frozen=True)
class ConstitutionReport:
    constitution_id: int
    constitution_name: str
    constitution_english: str
    category: str
    one_line: str
    body_signals: tuple[str, ...]
    tongue: str
    mind_summary: str
    mind_highlights: tuple[str, ...]
    disease_risk: tuple[str, ...]
    environment_note: str
    advice: Mapping[str, object]


@dataclass(frozen=True)
class TongueResult:
    """One observation's immutable fact: quality gate plus three downstream results.

    When the quality gate fails, ``recognition``/``constitution``/
    ``constitution_report`` are ``None``; ``quality`` is always present
    (carrying the selected frame's warnings and metrics).
    """

    quality: TongueQuality
    recognition: TongueRecognition | None
    constitution: ConstitutionAssessment | None
    constitution_report: ConstitutionReport | None
