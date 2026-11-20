"""Measurement public entry and lifetime for the upstream reader."""

import threading
from collections.abc import Callable

from server.compute.facephys.types import (
    VideoAnalysisInput,
    VideoPhysiologyReader,
    VideoPhysiologyMeasurement,
)


class TaskMeasurementReader:
    """任务作用域读取入口：每次调用在驻留资源上独占执行一次视频 pass。"""

    def __init__(self, run_pass: Callable[[VideoAnalysisInput | None],
                                          VideoPhysiologyMeasurement | None]):
        self._run_pass = run_pass

    def result(self, source: VideoAnalysisInput | None) -> VideoPhysiologyMeasurement | None:
        return self._run_pass(source)


class FacePhysService:
    """Serialize requests and prevent shutdown while the upstream pass is active."""

    def __init__(self, reader_factory: Callable[[], VideoPhysiologyReader]):
        self._reader_factory = reader_factory
        self._reader: VideoPhysiologyReader | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            self._reader = self._reader_factory()

    def close(self) -> None:
        with self._lock:
            self._reader = None

    def create_reader(self) -> VideoPhysiologyReader:
        return TaskMeasurementReader(self._run_pass)

    def _run_pass(self, source: VideoAnalysisInput | None) -> VideoPhysiologyMeasurement | None:
        with self._lock:
            if self._reader is None:
                raise RuntimeError("FacePhysService is not loaded")
            return self._reader.result(source)


__all__ = ["FacePhysService"]
