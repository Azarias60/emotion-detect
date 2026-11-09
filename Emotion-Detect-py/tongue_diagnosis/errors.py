"""舌象推理异常契约，无模型框架依赖。"""

class TonguePredictorError(Exception):
    """TonguePredictor 所有异常的基类"""
    pass


class ModelLoadError(TonguePredictorError):
    """模型加载失败（权重文件缺失、损坏、结构不匹配等）"""
    pass


class ImageNotFoundError(TonguePredictorError):
    """推理时指定的图片文件不存在"""
    pass


class InvalidImageError(TonguePredictorError):
    """图片文件无法正确解码"""
    pass


class InferenceError(TonguePredictorError):
    """模型推理过程中发生异常"""
    pass
