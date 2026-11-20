"""Connect timestamped capture frames to the upstream FacePhys runtime."""

from collections.abc import Callable
from itertools import chain

import cv2

from server.compute.facephys.vitals import finite_number, measure_vitals
from server.compute.facephys.types import (
    PhysiologyDiagnostics,
    VideoAnalysisInput,
    VideoHrvMeasurement,
    VideoPhysiologyMeasurement,
)


class OpenRppgReader:
    """Use a fresh upstream Model per sealed video, with its own inference state."""

    def __init__(self, model_factory: Callable):
        self._model_factory = model_factory

    def result(self, source: VideoAnalysisInput | None) -> VideoPhysiologyMeasurement | None:
        if source is None:
            return None
        frames = iter(source.frames())
        first = next(frames, None)
        if first is None:
            return None
        model = self._model_factory()

        origin = first[0]
        with model:
            for timestamp, frame in chain((first,), frames):
                model.update_frame(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), ts=timestamp - origin)

        result = measure_vitals(model, hr_window=None, hrv_window=None)
        summary = result["hrv_summary"]
        hrv = None
        if result["hrv_status"] == "available":
            hrv = VideoHrvMeasurement(
                rmssd_ms=summary["rmssd_ms"],
                sdnn_ms=summary["sdnn_ms"],
                pnn50_percent=summary["pnn50_percent"],
                lf_hf_ratio=summary["lf_hf_ratio"],
                nn50=summary["nn50"],
                rr_interval_count=summary["rr_interval_count"],
            )
        heart_rate = finite_number(result["hr"])
        respiration = result["breathing_rate_per_min"]
        return VideoPhysiologyMeasurement(
            heart_rate_bpm=heart_rate,
            respiration_rate_per_min=respiration,
            hrv=hrv,
            sqi=finite_number(result["SQI"]),
            diagnostics=PhysiologyDiagnostics(
                hrv_status=result["hrv_status"],
                respiration_status=result["respiration_status"],
                signal_quality=(result["hrv_signal_quality"]
                                if result["hrv_signal_quality"] is not None else finite_number(result["SQI"])),
                window_seconds=result["hrv_window_seconds"],
            ),
        )
