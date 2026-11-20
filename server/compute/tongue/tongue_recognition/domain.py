"""Four-dimensional tongue recognition policy over the published payloads.

``build_tongue_recognition`` projects the existing four-model output without
changing its values. Missing dimensions or fields intentionally raise
KeyError, matching the existing analysis path rather than silently fabricating
a prediction.
"""

from typing import Mapping

from server.compute.tongue.types import TongueDimensionResult, TongueRecognition


def build_tongue_recognition(
    predictions: Mapping[str, Mapping[str, object]],
) -> TongueRecognition:
    def dimension(name: str) -> TongueDimensionResult:
        prediction = predictions[name]
        return TongueDimensionResult(
            class_name=prediction["class"],
            probability=prediction["probability"],
        )

    return TongueRecognition(
        crack=dimension("crack"),
        coated=dimension("coated"),
        color=dimension("color"),
        indentation=dimension("indentation"),
    )
