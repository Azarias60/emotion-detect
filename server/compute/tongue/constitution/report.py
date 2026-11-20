"""Constitution report projection policy over the published payloads."""

from typing import Mapping

from server.compute.tongue.types import ConstitutionReport


def build_constitution_report(
    rules: Mapping[str, object], constitution_id: int,
) -> ConstitutionReport:
    """Project a typed report without depending on JSON, models, or Health."""
    constitution = rules["constitutions"].get(str(constitution_id))
    if not constitution:
        raise KeyError(f"未知体质ID: {constitution_id}")
    psychological = constitution["psychological"]
    body_signals = tuple(
        value for key, value in constitution["common_signs"].items()
        if key not in ("tongue", "pulse") and value
    )
    mind_highlights = []
    for key, label in (
        ("irritability", "易怒/烦躁"),
        ("anxiety", "焦虑/紧张"),
        ("overthinking", "思虑过度"),
        ("low_mood", "情绪低落"),
        ("sensitivity", "敏感多疑"),
    ):
        if psychological.get(key, 0) >= 2:
            mind_highlights.append(label)
    return ConstitutionReport(
        constitution_id=constitution_id,
        constitution_name=constitution["name"],
        constitution_english=constitution["english"],
        category="正常体质" if constitution["category"] == "normal" else "偏颇体质",
        one_line=constitution["key_features"],
        body_signals=body_signals,
        tongue=constitution["common_signs"].get("tongue", ""),
        mind_summary=psychological["summary"],
        mind_highlights=tuple(mind_highlights),
        disease_risk=tuple(constitution["disease_tendency"]),
        environment_note=constitution["environment"],
        advice=dict(rules["advice"].get(str(constitution_id), {})),
    )
