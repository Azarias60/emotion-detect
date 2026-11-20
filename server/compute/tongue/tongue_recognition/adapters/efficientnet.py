# -*-coding: utf-8 -*-
"""舌象四维推理引擎（4×EfficientNet-B0，自 tongue_diagnosis/efficient_net/predictor 平移）。

预处理 Resize(224)→CenterCrop(224)→ToTensor，无 mean/std 归一化；
4 个子模型的类别序由各自 class_indices.json 固化（coated 与 color 的顺序
互不相同，不得调换）。体质映射见 engines/constitution.py。
"""
import json
import os
import pickle

import torch
from PIL import Image, UnidentifiedImageError
from torchvision import transforms

from server import settings
from server.logutil import get_logger
from server.vendor.efficientnet.model import efficientnet_b0
from server.compute.tongue.tongue_recognition.errors import (
    ImageNotFoundError,
    InferenceError,
    InvalidImageError,
    ModelLoadError,
    TonguePredictorError,
)

logger = get_logger(__name__)


class TonguePredictor:
    """舌象诊断统一推理器：一次加载 4 个模型，predict() 无磁盘 IO。"""

    # (输出键, 权重目录名, 类别数)
    _MODEL_CONFIGS = [
        ("crack", "tongue_crack", 2),
        ("coated", "tongue_coated", 3),
        ("color", "tongue_color", 3),
        ("indentation", "tongue_indentation", 2),
    ]

    def __init__(self, device=None):
        self._base_dir = settings.TONGUE_WEIGHTS_DIR

        if device is not None:
            self._device = torch.device(device)
        else:
            self._device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        # EfficientNet-B0 设计输入 224×224；ToTensor 归一化到 [0,1]，无 mean/std。
        self._transform = transforms.Compose([
            transforms.Resize(224),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
        ])

        self.models = {}         # {name: nn.Module}
        self.class_indices = {}  # {name: {idx: label}}
        self._load_all_models()

        logger.info("TonguePredictor 初始化完成 (设备: %s, 已加载 %d 个模型)",
                    self._device, len(self.models))

    def _load_all_models(self):
        errors = []
        for name, dirname, num_classes in self._MODEL_CONFIGS:
            model_dir = os.path.join(self._base_dir, "tongue", dirname)
            try:
                json_path = os.path.join(model_dir, "class_indices.json")
                with open(json_path, "r", encoding="utf-8") as f:
                    class_indices = json.load(f)
            except (OSError, json.JSONDecodeError) as error:
                errors.append((name, error))
                continue

            model = efficientnet_b0(num_classes=num_classes)
            model.to(self._device)

            try:
                weight_path = os.path.join(model_dir, "weights", "model-29.pth")
                state_dict = torch.load(weight_path, map_location=self._device)
                model.load_state_dict(state_dict)
            except (OSError, EOFError, pickle.UnpicklingError, RuntimeError) as error:
                errors.append((name, error))
                continue
            # eval() 固定 BatchNorm 统计量、关闭 Dropout 随机性。
            model.eval()
            self.models[name] = model
            self.class_indices[name] = class_indices

        if errors:
            raise ModelLoadError(
                f"模型加载失败 ({len(errors)}/{len(self._MODEL_CONFIGS)}):\n" +
                "\n".join(f"  - [{name}] {error}" for name, error in errors)
            ) from errors[0][1]

    def predict(self, image_path):
        """对单张舌象图片执行四维特征检测，返回 {维度: {class, probability}}。"""
        if not os.path.exists(image_path):
            raise ImageNotFoundError(f"图片文件不存在: {image_path}")

        try:
            img = Image.open(image_path)
            # RGB 强制转换：PNG 可能是 RGBA/灰度，直接送入会 shape mismatch。
            img = img.convert("RGB")
        except UnidentifiedImageError:
            raise InvalidImageError(f"无法识别图片格式: {image_path}（文件可能已损坏或不是图片）")
        except OSError as e:
            raise InvalidImageError(f"图片加载失败: {image_path}\n原因: {e}")

        return self._infer(img)

    def predict_frame(self, frame):
        """对一帧 BGR ndarray 执行同一四维检测；规范帧免文件中转。"""
        import cv2 as _cv2

        if frame is None or frame.ndim != 3 or frame.shape[2] < 3:
            raise InvalidImageError("无法识别图片格式: 采集帧为空或不是三通道图像")
        return self._infer(Image.fromarray(_cv2.cvtColor(frame, _cv2.COLOR_BGR2RGB)))

    def _infer(self, img):
        img_tensor = self._transform(img).unsqueeze(0).to(self._device)

        results = {}
        name = None
        try:
            with torch.no_grad():
                for name in ["crack", "coated", "color", "indentation"]:
                    output = self.models[name](img_tensor)
                    output = output.squeeze(0).cpu()
                    probs = torch.softmax(output, dim=0)
                    cls_idx = torch.argmax(probs).item()
                    results[name] = {
                        "class": self.class_indices[name][str(cls_idx)],
                        "probability": round(probs[cls_idx].item(), 4),
                    }
                name = None
        except Exception as error:
            context = f"模型 [{name}] 推理失败" if name is not None else "推理过程异常"
            raise InferenceError(f"{context}: {error}") from error

        return results
