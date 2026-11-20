"""Public types owned by the tongue compute domain.

One observation entry (``TonguePort``) turns a frame factory into one
``TongueResult`` fact; the four members stay independent payloads. Callers
receive the typed payloads from this module instead of importing
sub-capability implementations.
"""

from server.compute.tongue.types.ports import TonguePort
from server.compute.tongue.types.results import (
    ConstitutionAssessment,
    ConstitutionCandidate,
    ConstitutionReport,
    TongueDimensionResult,
    TongueQuality,
    TongueRecognition,
    TongueResult,
)

__all__ = [
    "ConstitutionAssessment",
    "ConstitutionCandidate",
    "ConstitutionReport",
    "TongueDimensionResult",
    "TonguePort",
    "TongueQuality",
    "TongueRecognition",
    "TongueResult",
]
