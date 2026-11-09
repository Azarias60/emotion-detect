# predictor.py — 舌象四维特征统一推理接口
# ============================================================
# 功能:
#   封装 4 个 EfficientNet-B0 模型（裂纹/苔色/舌色/齿痕），提供统一推理接口。
#   初始化时一次加载全部模型，后续 predict() 调用无磁盘 IO。
#
# 输入: 舌象图片路径（.jpg/.png/.bmp 等常见格式）
# 输出: dict，四个模型的分类结果和置信度:
#   {
#     "crack":       {"class": "crack"|"normal",       "probability": 0.0~1.0},
#     "coated":      {"class": "white"|"yellow"|"nocoated", "probability": 0.0~1.0},
#     "color":       {"class": "red"|"white"|"crimson",     "probability": 0.0~1.0},
#     "indentation": {"class": "indentation"|"normal",      "probability": 0.0~1.0},
#   }
#
# 在数据流中的位置:
#   图片 → TonguePredictor.predict() → 四维舌象标签 → constitution_model → 体质报告
#          ↑────────── 本文件 ──────────↑
#
# 依赖 (向下):
#   - .efficientnet.model.efficientnet_b0    网络结构定义
#   - ./tongue_*/class_indices.json          4 个类别索引文件
#   - ./tongue_*/weights/model-29.pth        4 个权重文件 (各 ~16MB)
#   - PyTorch, torchvision, PIL
#
# 被依赖 (向上):
#   - core/lifecycle.py       (模型装载)
#   - services/tongue_service.py (舌象业务适配)
#
# 自定义异常:
#   TonguePredictorError   — 所有异常基类
#   ModelLoadError         — 模型加载失败
#   ImageNotFoundError     — 图片不存在
#   InvalidImageError      — 图片无法解码
#   InferenceError         — 推理过程异常
#
# 特点:
#   - 初始化时一次加载 4 个模型，后续推理无 IO 开销
#   - 所有路径基于 __file__，不依赖工作目录
#   - 自动检测 CUDA，也可手动指定 CPU
#   - 不修改原有 4 个 predict 文件和 model.py
#
# 用法:
#   from tongue_diagnosis.efficient_net.predictor import TonguePredictor
#   predictor = TonguePredictor(device="cpu")   # 或 device=None 自动选 CUDA
#   result = predictor.predict("图片路径.jpg")
#   json_str = predictor.predict_json("图片路径.jpg")
# ============================================================

import os
import json

import torch
from PIL import Image, UnidentifiedImageError
from torchvision import transforms

from tongue_diagnosis.errors import (
    TonguePredictorError,
    ModelLoadError,
    ImageNotFoundError,
    InvalidImageError,
    InferenceError,
)
from .efficientnet.model import efficientnet_b0


# ================================================================
# 推理器
# ================================================================

class TonguePredictor:
    """舌象诊断统一推理器

    加载裂纹、苔色、舌色、齿痕 4 个 EfficientNet-B0 模型，
    提供 predict() / predict_json() 接口对舌象图片进行四维特征检测。
    """

    # ---- 输出 JSON Schema（供外部联调方参考） ----
    OUTPUT_SCHEMA = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "TongueDiagnosisResult",
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["ok", "error"],
                "description": "推理状态：ok=成功，error=失败"
            },
            "crack": {
                "type": "object",
                "description": "裂纹检测结果",
                "properties": {
                    "class": {
                        "type": "string",
                        "enum": ["crack", "normal"],
                        "description": "crack=裂纹舌, normal=无裂纹"
                    },
                    "probability": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "预测置信度"
                    }
                }
            },
            "coated": {
                "type": "object",
                "description": "苔色检测结果",
                "properties": {
                    "class": {
                        "type": "string",
                        "enum": ["white", "yellow", "nocoated"],
                        "description": "white=白苔, yellow=黄苔, nocoated=无苔"
                    },
                    "probability": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0
                    }
                }
            },
            "color": {
                "type": "object",
                "description": "舌色检测结果",
                "properties": {
                    "class": {
                        "type": "string",
                        "enum": ["red", "white", "crimson"],
                        "description": "red=淡红舌, white=淡白舌, crimson=深红舌"
                    },
                    "probability": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0
                    }
                }
            },
            "indentation": {
                "type": "object",
                "description": "齿痕检测结果",
                "properties": {
                    "class": {
                        "type": "string",
                        "enum": ["indentation", "normal"],
                        "description": "indentation=齿痕舌, normal=无齿痕"
                    },
                    "probability": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0
                    }
                }
            }
        },
        "required": ["crack", "coated", "color", "indentation"]
    }

    def __init__(self, device=None):
        """
        函数作用:
            初始化推理器：确定推理设备 → 配置图像预处理 → 加载 4 个模型。
            这是整个系统唯一需要耗时的初始化步骤（~2s CPU / ~3s GPU）。

        输入:
            device: 推理设备。可选值:
                - None (默认): 自动检测 CUDA，不可用时回退 CPU
                - "cpu": 强制使用 CPU
                - "cuda:0": 使用指定 GPU

        输出: 无直接输出，初始化后 self.models / self.class_indices 字典填充完毕

        执行流程:
            1. 计算本文件所在目录的绝对路径（_base_dir），后续所有路径据此拼接
            2. 确定推理设备（CPU/CUDA）
            3. 构建图像预处理 pipeline:
               Resize(224) → CenterCrop(224) → ToTensor
               （与 4 个原始 predict 文件中完全一致）
            4. 遍历 _model_configs 中的 4 个配置，调用 _load_all_models()
            5. 打印初始化完成信息

        异常:
            ModelLoadError: 任一模型加载失败时抛出
        """
        # ---- 基础路径（基于本文件位置，不依赖 CWD） ----
        self._base_dir = os.path.dirname(os.path.abspath(__file__))

        # ---- 设备选择 ----
        if device is not None:
            self._device = torch.device(device)
        else:
            self._device = torch.device(
                "cuda:0" if torch.cuda.is_available() else "cpu"
            )

        # ---- 图像预处理（与 4 个 predict 文件完全一致） ----
        # 为什么是 224×224:
        #   EfficientNet 系列有固定的输入尺寸: B0=224, B1=240, ..., B7=600
        #   本项目使用 EfficientNet-B0，其设计输入就是 224×224。
        #   Resize 将图片短边缩到 224，CenterCrop 从中心裁出 224×224 正方形。
        #   ToTensor 将像素值从 [0,255] 归一化到 [0.0,1.0] 并转为 CHW 格式。
        self._transform = transforms.Compose([
            transforms.Resize(224),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
        ])

        # ---- 4 个模型配置 ----
        self._model_configs = [
            ("crack",       "tongue_crack",        2),
            ("coated",      "tongue_coated",       3),
            ("color",       "tongue_color",        3),
            ("indentation", "tongue_indentation",  2),
        ]

        self.models = {}         # {name: nn.Module}
        self.class_indices = {}  # {name: {idx: label}}

        self._load_all_models()

        print(f"TonguePredictor 初始化完成 (设备: {self._device}, "
              f"已加载 {len(self.models)} 个模型)")

    # ================================================================
    # 模型加载
    # ================================================================

    def _load_all_models(self):
        """
        函数作用:
            从磁盘加载 4 个 EfficientNet-B0 模型的结构定义和训练权重。
            此函数仅在 __init__ 中调用一次，后续 predict() 调用不产生磁盘 IO。

        输入: 无（通过 self._model_configs 和 self._base_dir 读取配置）

        输出: 无直接输出，填充 self.models 和 self.class_indices 两个字典

        执行流程:
            遍历 4 个模型配置 (crack/coated/color/indentation):
            1. 拼接模型目录路径 = _base_dir / tongue_xxx/
            2. 读取 class_indices.json → self.class_indices[name]
               例: {"0":"crack","1":"normal"}
            3. 创建 EfficientNet-B0 结构:
                efficientnet_b0(num_classes=N).to(device)
                N=2: crack/indentation, N=3: coated/color
            4. 加载权重文件:
                torch.load(weights/model-29.pth) → model.load_state_dict()
            5. 设为 eval 模式 → self.models[name]
            任一步失败时收集错误信息，全部遍历完后若有错误则抛出 ModelLoadError

        异常:
            ModelLoadError: 收集所有失败信息后统一抛出，包含具体文件路径和原因
        """
        errors = []

        for name, dirname, num_classes in self._model_configs:
            model_dir = os.path.join(self._base_dir, dirname)

            try:
                # ---- 读取类别索引 ----
                json_path = os.path.join(model_dir, "class_indices.json")
                if not os.path.exists(json_path):
                    raise FileNotFoundError(f"类别映射文件不存在: {json_path}")
                with open(json_path, "r", encoding="utf-8") as f:
                    self.class_indices[name] = json.load(f)

                # ---- 创建模型结构 ----
                model = efficientnet_b0(num_classes=num_classes)
                model.to(self._device)

                # ---- 加载训练权重 ----
                weight_path = os.path.join(model_dir, "weights", "model-29.pth")
                if not os.path.exists(weight_path):
                    raise FileNotFoundError(f"权重文件不存在: {weight_path}")

                state_dict = torch.load(weight_path, map_location=self._device)
                model.load_state_dict(state_dict)

                # model.eval() 的作用:
                #   将模型从训练模式切换为推理模式。
                #   关键影响: 固定 BatchNorm 的 running_mean/running_var，
                #   关闭 Dropout 的随机丢弃。
                #   不调用 eval() 会导致每次推理结果略有不同 (Dropout 随机性)
                #   且 BatchNorm 统计量会被单张图片污染。
                model.eval()

                self.models[name] = model

            except (FileNotFoundError, json.JSONDecodeError,
                    RuntimeError, KeyError) as e:
                errors.append(f"[{name}] {e}")

        if errors:
            raise ModelLoadError(
                f"模型加载失败 ({len(errors)}/{len(self._model_configs)}):\n" +
                "\n".join(f"  - {e}" for e in errors)
            )

    # ================================================================
    # 推理接口
    # ================================================================

    def predict(self, image_path):
        """
        函数作用:
            对单张舌象图片执行四维特征检测，返回每个模型的分类结果和置信度。

        输入:
            image_path: 舌象图片文件路径 (str)，支持 .jpg/.png/.bmp 等常见格式

        返回:
            dict: {
                "crack":        {"class": "crack"|"normal",
                                 "probability": 0.0~1.0},
                "coated":       {"class": "white"|"yellow"|"nocoated",
                                 "probability": 0.0~1.0},
                "color":        {"class": "red"|"white"|"crimson",
                                 "probability": 0.0~1.0},
                "indentation":  {"class": "indentation"|"normal",
                                 "probability": 0.0~1.0},
            }

        执行流程:
            1. 文件存在性检查 → 不存在则抛出 ImageNotFoundError
            2. 图片加载: PIL.Image.open() → .convert("RGB") 统一为三通道
               → 失败则抛出 InvalidImageError
            3. 预处理: Resize(224) → CenterCrop(224) → ToTensor → unsqueeze(0)
               → 得到 [1, 3, 224, 224] 的 tensor
            4. 依次对 4 个模型执行推理 (torch.no_grad):
               forward → squeeze(0) → softmax → argmax
               → 查 class_indices 表得到英文标签
               → 取最大概率值保留 4 位小数
            5. 返回结构化结果字典

        异常:
            ImageNotFoundError: 图片文件不存在
            InvalidImageError:  图片文件无法解码或预处理失败
            InferenceError:     模型推理过程异常
        """
        # ---- 1. 文件存在性检查 ----
        if not os.path.exists(image_path):
            raise ImageNotFoundError(f"图片文件不存在: {image_path}")

        # ---- 2. 图片加载 ----
        try:
            img = Image.open(image_path)
            # 强制转换为 RGB 三通道
            # 原因: EfficientNet-B0 的 stem_conv 层定义为 in_planes=3（RGB 三通道）。
            #       PNG 可能是 RGBA (4 通道) 或灰度 (1 通道)，直接送入会报 shape mismatch。
            #       .convert("RGB") 会自动处理: RGBA→RGB (丢弃 alpha), L→RGB (复制灰度到三通道)
            img = img.convert("RGB")
        except UnidentifiedImageError:
            raise InvalidImageError(
                f"无法识别图片格式: {image_path}（文件可能已损坏或不是图片）")
        except Exception as e:
            raise InvalidImageError(
                f"图片加载失败: {image_path}\n原因: {e}")

        # ---- 3. 预处理 ----
        try:
            img_tensor = self._transform(img).unsqueeze(0).to(self._device)
        except Exception as e:
            raise InvalidImageError(
                f"图片预处理失败: {image_path}\n原因: {e}")

        # ---- 4. 模型推理 ----
        results = {}
        try:
            # torch.no_grad() 的作用:
            #   关闭 PyTorch 的自动梯度计算 (autograd)。
            #   推理时不需要反向传播，关闭梯度计算可以:
            #   (a) 节省显存/内存 — 不保存中间激活值
            #   (b) 加速计算 — 跳过梯度图构建
            #   不关闭的话 4 个模型会各自保留完整计算图，显存翻 4 倍以上。
            with torch.no_grad():
                for name in ["crack", "coated", "color", "indentation"]:
                    try:
                        # 单模型推理的 5 个步骤:
                        # ① forward: 输入 [1,3,224,224] → 输出 [1, num_classes]
                        output = self.models[name](img_tensor)
                        # ② squeeze(0): 去掉 batch 维度 → [num_classes]
                        output = output.squeeze(0).cpu()
                        # ③ softmax: logits → 概率分布 (各项和为 1.0)
                        probs = torch.softmax(output, dim=0)
                        # ④ argmax: 取最大概率的索引
                        cls_idx = torch.argmax(probs).item()
                        # ⑤ 用 class_indices 表将数字索引转为英文标签
                        #    例: class_indices["crack"] = {"0":"crack","1":"normal"}
                        #         cls_idx=0 → "crack"
                        results[name] = {
                            "class": self.class_indices[name][str(cls_idx)],
                            "probability": round(probs[cls_idx].item(), 4),
                        }
                    except Exception as e:
                        raise InferenceError(
                            f"模型 [{name}] 推理失败: {e}")
        except InferenceError:
            raise
        except Exception as e:
            raise InferenceError(f"推理过程异常: {e}")

        return results

    # ================================================================
    # JSON 序列化接口
    # ================================================================

    def predict_json(self, image_path, indent=2):
        """对单张图片执行推理，返回 JSON 字符串

        参数:
            image_path: 图片路径
            indent:     JSON 缩进空格数（None 表示紧凑输出）

        返回:
            str: JSON 字符串，格式见 OUTPUT_SCHEMA

        异常:
            同 predict()
        """
        try:
            result = self.predict(image_path)
            result["status"] = "ok"
        except TonguePredictorError as e:
            result = {
                "status": "error",
                "error_type": type(e).__name__,
                "error_message": str(e),
            }
        except Exception as e:
            result = {
                "status": "error",
                "error_type": "UnknownError",
                "error_message": str(e),
            }

        return json.dumps(result, ensure_ascii=False, indent=indent)

    # ================================================================
    # 工具方法
    # ================================================================

    @property
    def device_name(self):
        """当前推理设备名称"""
        return str(self._device)

    @property
    def model_names(self):
        """已加载的模型名称列表"""
        return list(self.models.keys())

    @property
    def model_count(self):
        """已加载的模型数量"""
        return len(self.models)

    @staticmethod
    def get_output_schema():
        """返回输出 JSON Schema（供联调方参考）"""
        return json.dumps(TonguePredictor.OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
