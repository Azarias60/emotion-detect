"""视频算法的兼容入口；模型依赖按需加载。"""


def analyze_fatigue_video(*args, **kwargs):
    from .fatigue_v03 import analyze_fatigue_video as analyze
    return analyze(*args, **kwargs)


def analyze_hrv_video(*args, **kwargs):
    from .kde_hrv import analyze_hrv_video as analyze
    return analyze(*args, **kwargs)


__all__ = ["analyze_fatigue_video", "analyze_hrv_video"]
