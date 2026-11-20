"""Tongue image quality use-case-owned input boundary.

The reader supplies measurements only; it does not classify image quality.
Concrete image decoders belong to outer adapters.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TongueImageMetrics:
    width: int
    height: int
    brightness: float
    sharpness: float


class TongueImageMetricsReader(Protocol):
    def read(self, image_path: str) -> TongueImageMetrics: ...

    def read_image(self, image) -> TongueImageMetrics: ...
