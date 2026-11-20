"""Pure constitution assessment policy over the published payloads."""

from typing import Mapping

from server.compute.tongue.types import ConstitutionAssessment, ConstitutionCandidate


def infer_constitution_candidates(
    rules: Mapping[str, object], *, color_class: str, coated_class: str,
    crack_class: str, indent_class: str,
) -> tuple[ConstitutionCandidate, ...]:
    """Apply the first matching four-feature rule and descending-weight order."""
    key = (color_class, coated_class, crack_class, indent_class)
    candidates = None
    for entry in rules["tongue_rules"]:
        if tuple(entry[:4]) == key:
            candidates = entry[4]
            break
    if not candidates:
        return ()
    return tuple(
        ConstitutionCandidate(
            id=cid,
            name=rules["constitution_types"].get(str(cid), f"未知{cid}"),
            english=rules["constitution_english"].get(str(cid), ""),
            weight=weight,
            category=rules["constitutions"].get(str(cid), {}).get("category", "unknown"),
        )
        for cid, weight in sorted(candidates, key=lambda item: -item[1])
    )
