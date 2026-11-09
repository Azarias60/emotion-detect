"""舌象画质检查、四维分类和体质报告组装。"""
import cv2
import numpy as np

from tongue_diagnosis.constitution_model import get_body_mind_report, infer_constitution_from_tongue

TONGUE_LABELS = {
    "color": {"red": "淡红舌", "white": "淡白舌", "crimson": "深红舌"},
    "coated": {"white": "白苔", "yellow": "黄苔", "nocoated": "无苔"},
    "crack": {"crack": "裂纹舌", "normal": "无裂纹"},
    "indentation": {"indentation": "齿痕舌", "normal": "无齿痕"},
}


def inspect_tongue_image(image_path: str) -> dict:
    """舌象图片前置画质检测（分辨率、亮度、清晰度）。"""
    image = cv2.imread(image_path)
    if image is None:
        raise ValueError("无法解析上传的图片")
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    brightness = float(np.mean(gray))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    warnings = []
    if min(width, height) < 224:
        warnings.append("图片分辨率较低，请靠近摄像头重新拍摄")
    if brightness < 45:
        warnings.append("画面偏暗，请增加正面光线")
    elif brightness > 225:
        warnings.append("画面可能过曝，请避免强光直射")
    if sharpness < 35:
        warnings.append("画面较模糊，请保持舌头和摄像头稳定")
    return {
        "passed": not warnings,
        "warnings": warnings,
        "width": width,
        "height": height,
        "brightness": round(brightness, 1),
        "sharpness": round(sharpness, 1),
    }


def analyze_tongue_image(image_path: str, *, predictor, lock) -> dict:
    """执行舌象四维分类与中医体质辨识。"""
    if predictor is None:
        raise RuntimeError("舌象模型尚未加载")

    quality = inspect_tongue_image(image_path)
    with lock:
        raw_result = predictor.predict(image_path)

    tongue_result = {}
    for dimension, prediction in raw_result.items():
        class_name = prediction["class"]
        tongue_result[dimension] = {
            **prediction,
            "label": TONGUE_LABELS.get(dimension, {}).get(class_name, class_name),
        }

    candidates = infer_constitution_from_tongue(
        raw_result["color"]["class"],
        raw_result["coated"]["class"],
        raw_result["crack"]["class"],
        raw_result["indentation"]["class"],
    )
    primary_report = get_body_mind_report(candidates[0]["id"]) if candidates else {}
    return {
        "tongue": tongue_result,
        "constitution_candidates": candidates,
        "report": primary_report,
        "quality": quality,
        "disclaimer": "舌象结果仅供健康参考，不能替代专业医疗诊断。",
    }
