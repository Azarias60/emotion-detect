"""Tongue image quality contract and legacy threshold/rounding parity."""

from dataclasses import FrozenInstanceError

import pytest

from server.compute.tongue.types import TongueQuality
from server.compute.tongue.tongue_quality.domain import assess_tongue_image_quality


@pytest.mark.parametrize(
    "width,height,brightness,sharpness,warnings",
    [
        (224, 224, 45.0, 35.0, ()),
        (223, 224, 45.0, 35.0, ("图片分辨率较低，请靠近摄像头重新拍摄",)),
        (224, 223, 45.0, 35.0, ("图片分辨率较低，请靠近摄像头重新拍摄",)),
        (224, 224, 44.999, 35.0, ("画面偏暗，请增加正面光线",)),
        (224, 224, 225.0, 35.0, ()),
        (224, 224, 225.001, 35.0, ("画面可能过曝，请避免强光直射",)),
        (224, 224, 45.0, 34.999, ("画面较模糊，请保持舌头和摄像头稳定",)),
        (100, 150, 44.9, 34.9, (
            "图片分辨率较低，请靠近摄像头重新拍摄",
            "画面偏暗，请增加正面光线",
            "画面较模糊，请保持舌头和摄像头稳定",
        )),
    ],
)
def test_quality_thresholds(width, height, brightness, sharpness, warnings):
    result = assess_tongue_image_quality(
        width=width, height=height, brightness=brightness, sharpness=sharpness,
    )
    assert isinstance(result, TongueQuality)
    assert result.warnings == warnings
    assert result.passed is (not warnings)
    assert result.width == width and result.height == height
    assert result.brightness == round(brightness, 1)
    assert result.sharpness == round(sharpness, 1)


def test_quality_result_is_immutable():
    result = assess_tongue_image_quality(width=224, height=224, brightness=120, sharpness=40)
    with pytest.raises(FrozenInstanceError):
        result.passed = False


def test_quality_compares_before_rounding():
    result = assess_tongue_image_quality(width=224, height=224, brightness=44.999, sharpness=34.999)
    assert result.brightness == 45.0 and result.sharpness == 35.0
    assert result.warnings == ("画面偏暗，请增加正面光线", "画面较模糊，请保持舌头和摄像头稳定")
