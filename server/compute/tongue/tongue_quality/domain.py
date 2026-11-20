"""Pure tongue image quality policy, independent of image decoding and models.

Metrics are supplied by an outer image adapter. Threshold comparisons use
unrounded metrics; rounding applies only to the public presentation values.
"""

from server.compute.tongue.types import TongueQuality


def assess_tongue_image_quality(
    *, width: int, height: int, brightness: float, sharpness: float,
) -> TongueQuality:
    """Assess existing resolution, brightness and blur thresholds in order."""
    warnings = []
    if min(width, height) < 224:
        warnings.append("图片分辨率较低，请靠近摄像头重新拍摄")
    if brightness < 45:
        warnings.append("画面偏暗，请增加正面光线")
    elif brightness > 225:
        warnings.append("画面可能过曝，请避免强光直射")
    if sharpness < 35:
        warnings.append("画面较模糊，请保持舌头和摄像头稳定")
    return TongueQuality(
        passed=not warnings,
        warnings=tuple(warnings),
        width=width,
        height=height,
        brightness=round(brightness, 1),
        sharpness=round(sharpness, 1),
    )
