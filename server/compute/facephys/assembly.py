"""Bind the FacePhys ONNX model to the upstream video pipeline."""

from functools import partial
from pathlib import Path

from server import settings
from server.compute.facephys.onnx import load_onnx_step
from server.compute.facephys.service import FacePhysService
from server.compute.facephys.upstream import OpenRppgReader


def create_open_rppg_reader() -> OpenRppgReader:
    from rppg import Model
    step, initial_state = load_onnx_step(Path(settings.WEIGHTS_DIR) / "facephys" / "model.onnx")
    model = partial(Model, step, initial_state, {"fps": 30.0, "input": (1, 36, 36, 3)})
    return OpenRppgReader(model)


def create_facephys_service() -> FacePhysService:
    return FacePhysService(create_open_rppg_reader)
