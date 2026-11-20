"""One bounded device recording. Offsets are relative to admission on the capture clock."""

from server import settings
from server.capture.domain import AudioWindow
from server.detection_errors import DetectionInputError


class Recording:
    def __init__(self, clock, sensors, video_window, resample, release, connection_state=None):
        self.started = clock()
        self.deadline = self.started + settings.CAPTURE_WINDOW_SECONDS
        self.end = None
        self.frames = []
        self.audio = []
        self._video_window = video_window
        self._resample = resample
        self._clock = clock
        self._release = release
        self._connection_state = connection_state
        self.sensors = sensors.new_window(self._sensor_time)
        self.connected = False
        self.invalid = False

    def _sensor_time(self):
        return self.end if self.end is not None else min(self._clock(), self.deadline)

    def receiving(self, now):
        return not self.invalid and self.end is None and self.started <= now < self.deadline

    def position_ms(self):
        return (self._sensor_time() - self.started) * 1000

    def seal(self, connected=None):
        """封存本轮采集：窗口右端固定，返回封存输入供测量与分析消费。

        ``connected`` 缺省时由采集能力自证（设备链路状态在封存时刻读取），
        调度不必探询设备连接。
        """
        if self.invalid:
            raise DetectionInputError("本轮采集期间设备已换代，请重新检测")
        if self.end is None:
            self.end = min(self.deadline, self._clock())
            self.connected = self._connection_state() if connected is None and self._connection_state else bool(connected)
        return self

    def video_window(self):
        return self._video_window(tuple(item for item in self.frames if item[0] < self.end))

    def is_connected(self):
        return self.connected

    def release(self):
        """结束本轮记录的采集使用期；窗口数据仍归报告与测量消费。"""
        self._release(self)

    def audio_window(self, *, sample_rate):
        return self.audio_segment(0, (self.end - self.started) * 1000, sample_rate)

    def audio_segment(self, start_ms, end_ms, sample_rate):
        if self.invalid:
            raise DetectionInputError("本轮采集期间设备已换代，请重新检测")
        start = self.started + start_ms / 1000
        end = min(self.started + end_ms / 1000, self._sensor_time())
        pcm = b"".join(chunk for at, chunk in self.audio if start <= at < end)
        if not pcm:
            return None
        return AudioWindow(self._resample(pcm, sample_rate), sample_rate)

    def heart_rate_window(self):
        return self.sensors.heart_rate_window()

    def temperature_window(self):
        return self.sensors.temperature_window()
