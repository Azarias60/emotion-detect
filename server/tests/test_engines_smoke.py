# -*-coding: utf-8 -*-
"""Model smoke gates and Result-native constitution assessment contracts."""

import json
import os
import tempfile

import numpy as np
import pytest

from server import settings
from server.compute.tongue.constitution.adapters.json_rules import JsonConstitutionRulesReader
from server.compute.tongue.service import (
    ConstitutionAssessmentApplication,
    ConstitutionReportApplication,
)
from server.compute.tongue.types import ConstitutionReport
from server.compute.speech.speech.domain import segment_transcript
from server.compute.tongue.types import TongueDimensionResult, TongueRecognition


def test_rules_json_loads():
    data = JsonConstitutionRulesReader().read()
    assert len(data["tongue_rules"]) == 36
    assert len(data["constitutions"]) == 9
    assert len(data["advice"]) == 9


def test_voice_segment_semantics_pinned():
    segments = segment_transcript("<|zh|><|HAPPY|>今天天气很好。心情不错。")
    assert tuple(segment.text for segment in segments) == ("今天天气很好。 心情不错。",)
    assert all(not hasattr(segment, "emotion") for segment in segments)
    assert tuple(segment.text for segment in segment_transcript("<|en|>hello world")) == ("hello world",)
    assert segment_transcript("") == ()
    assert segment_transcript("<|en|><|NEUTRAL|>") == ()
    result = segment_transcript("甲。乙。丙！")
    assert tuple(segment.text for segment in result) == ("甲。 乙。", "丙！")


@pytest.mark.model
def test_face_engine_strict_load_and_class_order():
    from server.compute.expression.assembly import EMOTION_CLASSES, create_emotion_predictor

    predictor = create_emotion_predictor()
    assert EMOTION_CLASSES == ["angry", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    result = predictor.task(image)
    assert result.observations == ()
    assert result.class_counts == ()


@pytest.mark.model
def test_tongue_predictor_frame_input_matches_the_jpeg_file_path():
    """阶段四：设备抓拍帧直入模型与旧 JPEG 文件路径结果一致（免有损落盘）。"""
    import cv2

    from server.compute.tongue.tongue_recognition.adapters.efficientnet import TonguePredictor

    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    frame[40:260, 60:240] = (60, 40, 180)  # 近舌质区域，避免全黑退化输入

    predictor = TonguePredictor(device="cpu")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "capture.jpg")
        assert cv2.imwrite(path, frame)
        file_result = predictor.predict(path)
    frame_result = predictor.predict_frame(frame)

    assert set(file_result) == set(frame_result)
    for dimension, file_payload in file_result.items():
        frame_payload = frame_result[dimension]
        assert file_payload["class"] == frame_payload["class"], dimension
        # 差异仅来自旧链路的 JPEG 有损落盘；类别判定不受影响
        assert abs(file_payload["probability"] - frame_payload["probability"]) <= 0.05


@pytest.mark.model
def test_tongue_engine_strict_load_and_predict_vocab():
    from server.compute.tongue.tongue_recognition.adapters.efficientnet import TonguePredictor

    predictor = TonguePredictor(device="cpu")
    assert set(predictor.models) == {"crack", "coated", "color", "indentation"}
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "tongue.jpg")
        import cv2
        cv2.imwrite(path, np.zeros((300, 300, 3), dtype=np.uint8))
        result = predictor.predict(path)
    vocab = {
        "crack": {"crack", "normal"},
        "coated": {"white", "yellow", "nocoated"},
        "color": {"red", "white", "crimson"},
        "indentation": {"indentation", "normal"},
    }
    for dimension, prediction in result.items():
        assert prediction["class"] in vocab[dimension]
        assert 0.0 <= prediction["probability"] <= 1.0


def _recognition(color, coated, crack, indentation):
    return TongueRecognition(
        crack=TongueDimensionResult(crack, 0.8),
        coated=TongueDimensionResult(coated, 0.8),
        color=TongueDimensionResult(color, 0.8),
        indentation=TongueDimensionResult(indentation, 0.8),
    )


def test_constitution_mapping_full_coverage():
    """All 3×3×2×2 combinations are assessed through the typed payload."""
    app = ConstitutionAssessmentApplication(JsonConstitutionRulesReader())
    for color in ("red", "white", "crimson"):
        for coated in ("white", "yellow", "nocoated"):
            for crack in ("crack", "normal"):
                for indent in ("indentation", "normal"):
                    result = app.result(_recognition(color, coated, crack, indent))
                    assert result is not None and result.candidates, (
                        f"规则缺失: {(color, coated, crack, indent)}"
                    )
                    weights = [candidate.weight for candidate in result.candidates]
                    assert weights == sorted(weights, reverse=True)


def _assessment(constitution_id):
    from server.compute.tongue.types import ConstitutionAssessment, ConstitutionCandidate

    return ConstitutionAssessment((
        ConstitutionCandidate(
            id=constitution_id, name="", english="", weight=1.0, category="normal",
        ),
    ))


@pytest.mark.parametrize("constitution_id", range(1, 10))
def test_body_mind_report_is_a_typed_result(constitution_id):
    result = ConstitutionReportApplication(
        JsonConstitutionRulesReader(),
    ).result(_assessment(constitution_id))
    assert isinstance(result, ConstitutionReport)
    assert result.constitution_name
    assert result.category in ("正常体质", "偏颇体质")
    assert set(result.advice) == {"diet", "exercise", "lifestyle", "mind"}
    assert result.advice["diet"]


def test_unknown_constitution_report_is_not_encoded_as_a_legacy_dictionary():
    with pytest.raises(KeyError, match="未知体质ID: 99"):
        ConstitutionReportApplication(JsonConstitutionRulesReader()).result(_assessment(99))
