"""FacePhys-Release ONNX step adapted to the Open-rppg inference port."""

from pathlib import Path

import numpy as np


def load_onnx_step(weights: Path):
    import onnxruntime as ort

    if not weights.is_file():
        raise FileNotFoundError(weights)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.log_severity_level = 3
    session = ort.InferenceSession(
        str(weights), sess_options=options, providers=["CPUExecutionProvider"],
    )
    state = {
        input_node.name: np.zeros(input_node.shape, dtype=np.float32)
        for input_node in session.get_inputs()[2:]
    }
    dt = np.array([1 / 30], dtype=np.float32)

    def step(frames, current_state):
        inputs = frames.astype(np.float32)[None] / np.float32(255)
        bvp, *updated = session.run(None, {"input": inputs, "dt": dt, **current_state})
        return {"bvp": bvp[0]}, dict(zip(current_state, updated, strict=True))

    return step, state
