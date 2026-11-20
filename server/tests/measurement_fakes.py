"""测量域共享测试替身：规范化的一次成功视频测量结果与读取器。"""

from server.compute.facephys.types import (
    VideoHrvMeasurement,
    VideoPhysiologyMeasurement,
)


def physiology_ok() -> VideoPhysiologyMeasurement:
    """一次 72.6 BPM、呼吸 15.3、全量 HRV 的成功测量。"""
    return VideoPhysiologyMeasurement(
        heart_rate_bpm=72.6,
        respiration_rate_per_min=15.3,
        hrv=VideoHrvMeasurement(
            sdnn_ms=41.2, rmssd_ms=33.8, nn50=6, pnn50_percent=18.4,
            lf_hf_ratio=1.7, rr_interval_count=3,
        ),
        sqi=0.86,
        face_coverage=0.95,
    )


class FakePhysiologyReader:
    """记录输入的读取器替身；按脚本返回结果或抛错。"""

    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = []

    def result(self, source):
        self.calls.append(source)
        if self.error is not None:
            raise self.error
        return self.value
