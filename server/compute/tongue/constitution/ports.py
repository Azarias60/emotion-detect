"""Constitution assessment's outbound dependency, owned by the use case."""

from typing import Mapping, Protocol


class ConstitutionRulesReader(Protocol):
    """Supply the current constitution rule table without exposing its storage."""

    def read(self) -> Mapping[str, object]: ...
