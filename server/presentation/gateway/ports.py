"""Explicit capability Ports used by the observation Gateway routes.

The Gateway depends on these structural contracts only; composition supplies
concrete adapters. No web framework, runtime container, or capability bootstrap
is imported here. Result payload types and the single-capability port protocols
are owned by each compute domain's contracts.
"""

from dataclasses import dataclass
from typing import Protocol

from server.analysis.fatigue.types import BehaviorObservationFlowPort
from server.capture.types import CapturePort
from server.compute.expression.types import ExpressionPort
from server.compute.speech.types import SpeechPort
from server.concurrency import TaskAdmissionPort


@dataclass(frozen=True)
class GatewayPorts:
    """Frozen transport-composition bundle passed to the route factory."""

    admission: TaskAdmissionPort
    capture: CapturePort
    expression: ExpressionPort
    behavior_observation_flow: BehaviorObservationFlowPort
    speech: SpeechPort


__all__ = [
    "BehaviorObservationFlowPort",
    "GatewayPorts",
    "TaskAdmissionPort",
    "CapturePort",
    "ExpressionPort",
    "SpeechPort",
]
