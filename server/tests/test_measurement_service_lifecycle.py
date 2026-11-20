"""Measurement service readiness, serialization and shutdown."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from server.compute.facephys.service import FacePhysService
from server.compute.facephys.types import VideoPhysiologyMeasurement


class Reader:
    def result(self, source):
        return VideoPhysiologyMeasurement(heart_rate_bpm=72.0)


def test_readers_follow_the_service_lifetime():
    service = FacePhysService(Reader)
    reader = service.create_reader()
    with pytest.raises(RuntimeError, match="FacePhysService is not loaded"):
        reader.result(None)
    service.load()
    assert reader.result(None).heart_rate_bpm == 72.0
    service.close()
    with pytest.raises(RuntimeError, match="FacePhysService is not loaded"):
        reader.result(None)


def test_shutdown_waits_for_the_active_measurement_pass():
    started, release, closing = (threading.Event() for _ in range(3))
    events = []

    class SlowReader(Reader):
        def result(self, source):
            started.set()
            assert release.wait(3)
            events.append("pass_finished")
            return super().result(source)

    service = FacePhysService(SlowReader)
    service.load()

    def close():
        closing.set()
        service.close()
        events.append("closed")

    with ThreadPoolExecutor(max_workers=2) as executor:
        result = executor.submit(service.create_reader().result, None)
        try:
            assert started.wait(3)
            shutdown = executor.submit(close)
            assert closing.wait(3)
            assert not shutdown.done()
        finally:
            release.set()
        assert result.result(3).heart_rate_bpm == 72.0
        shutdown.result(3)
    assert events == ["pass_finished", "closed"]
