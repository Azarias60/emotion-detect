###############################################################################
#  WebRTC / RTCPush 输出模块 — 通过 HumanPlayer 推流
###############################################################################

from numpy.typing import NDArray
import numpy as np

from registry import register
from streamout.base_output import BaseOutput
from utils.logger import logger


@register("streamout", "webrtc")
@register("streamout", "rtcpush")
class WebRTCOutput(BaseOutput):
    """WebRTC / RTCPush 输出：将视频/音频帧通过 HumanPlayer 推送到 aiortc 轨道。"""

    def __init__(self, opt=None, parent=None, **kwargs):
        super().__init__(opt, parent)
        self._player = None

    def start(self) -> None:
        logger.info("[WebRTCOutput] 已启动（等待 WebRTC 连接建立）")

    def push_video_frame(self, frame: NDArray[np.uint8]) -> None:
        if not isinstance(frame, np.ndarray):
            return

        if self._player is not None:
            self._player.push_video(frame)

    def push_audio_frame(self, frame: NDArray[np.int16], eventpoint=None) -> None:
        if self._player is not None:
            self._player.push_audio(frame, eventpoint)
        # 这里**不再**调 self.parent.notify(eventpoint)：
        # WebRTC 的段落通知由 PlayerStreamTrack.recv（server/webrtc.py）在
        # 帧真正发给浏览器的那一刻打；在这里再打一次，同一条 TTS 段落会被
        # 记两次（实测 548 条 notify 日志 / 149 次实际播报），日志量翻倍且
        # 干扰排查。而且刚推入队列的帧可能排队几百毫秒、甚至被
        # clear_buffer() 直接丢掉 —— "已上屏"的时刻只有发送端知道。

    def get_buffer_size(self) -> int:
        if self._player is not None:
            return self._player.get_buffer_size()
        return 0

    def clear_buffer(self) -> None:
        if self._player is not None:
            self._player.clear_queues()

    def stop(self) -> None:
        logger.info("[WebRTCOutput] 已停止")
