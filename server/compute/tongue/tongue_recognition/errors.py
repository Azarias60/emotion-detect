"""Health 领域错误定义。

异常本身不依赖模型框架或具体 Adapter，供应用层、Adapter 与测试共享。
"""


class TonguePredictorError(Exception):
    """TonguePredictor 所有异常的基类。"""


class ModelLoadError(TonguePredictorError):
    """模型加载失败（权重文件缺失、损坏、结构不匹配等）。"""


class ImageNotFoundError(TonguePredictorError):
    """推理时指定的图片文件不存在。"""


class InvalidImageError(TonguePredictorError):
    """图片文件无法正确解码。"""


class InferenceError(TonguePredictorError):
    """模型推理过程中发生异常。"""


__all__ = [
    "TonguePredictorError",
    "ModelLoadError",
    "ImageNotFoundError",
    "InvalidImageError",
    "InferenceError",
]
