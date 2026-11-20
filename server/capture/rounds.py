"""检测轮次的采集接入：begin 建立本轮记录，问答经 active() 消费同源音频。

同一设备同一时间只有一轮采集（内核忙时拒绝并发 begin）；封存与释放
仍是本轮记录自身的操作，轮次接入不拥有业务时序。
"""


class CaptureRounds:
    def __init__(self, kernel):
        self._kernel = kernel

    def begin(self):
        """开始一轮采集记录；设备离线或忙时抛出检测域错误。"""
        return self._kernel.begin_recording()

    def active(self):
        """当前轮次记录；无轮次返回 None。"""
        return self._kernel.current_recording()


__all__ = ["CaptureRounds"]
