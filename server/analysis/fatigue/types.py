"""Public contract for independent behavior observation."""

from typing import Protocol

from server.compute.fatigue.types import BehaviorObservation


class BehaviorObservationFlowPort(Protocol):
    async def observe_behavior(self) -> BehaviorObservation: ...
