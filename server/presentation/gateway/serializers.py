"""Transport projections for observation payloads.

Each function is an operation-specific serializer: capability payloads enter
the Gateway, JSON-safe mappings leave it. Field selection mirrors the already
published wire contract; business formulas are not duplicated here. 检测与
报告事实的投影归报告路径（``server.detection.flows``），不在此重复。
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass

from server.analysis.emotion.facts import expression_fact
from server.analysis.facts import (
    behavior_observation_fact,
    speech_fact,
)
from server.compute.expression.types import ExpressionObservations
from server.compute.fatigue.types import BehaviorObservation
from server.compute.speech.types import Transcript
from server.envelope import JSONValue


def _jsonable(value: object) -> JSONValue:
    """Convert transport values to JSON primitives without losing fields."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    raise TypeError(f"cannot serialize {type(value).__name__} to JSON")


def _json_object(value: object) -> Mapping[str, JSONValue]:
    body = _jsonable(value)
    if not isinstance(body, Mapping):
        raise TypeError("serializer expected a JSON object")
    return body


def serialize_expression_result(
    result: ExpressionObservations | None,
) -> Mapping[str, JSONValue] | None:
    if result is None:
        return None
    return _json_object(expression_fact(result))


def serialize_behavior_result(
    result: BehaviorObservation,
) -> Mapping[str, JSONValue]:
    return _json_object(behavior_observation_fact(result))


def serialize_speech_result(
    result: Transcript | None,
) -> Mapping[str, JSONValue] | None:
    if result is None:
        return None
    return _json_object(speech_fact(result))


__all__ = [
    "serialize_behavior_result",
    "serialize_expression_result",
    "serialize_speech_result",
]
