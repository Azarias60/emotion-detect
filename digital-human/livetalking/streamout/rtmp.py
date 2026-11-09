###############################################################################
#  RTMP 推流输出模块
#  需要安装 PyAV 并通过 FFmpeg 推流到 RTMP 服务器
###############################################################################

import subprocess
import time
from numpy.typing import NDArray
import numpy as np

from registry import register
from streamout.base_output import BaseOutput
from utils.logger import logger


@register("streamout", "rtmp")
class RTMPOutput(BaseOutput):
    """RTMP 推流输出：通过 ffmpeg 管道推流到 RTMP 服务器。"""

    def __init__(self, opt=None, parent=None, **kwargs):
        super().__init__(opt, parent)
        self._ffmpeg_proc = None
        self._push_url = getattr(opt, "push_url", "")
        self._width = 0
        self._height = 0
        self._fps = getattr(opt, "fps", 25)

    def start(self) -> None:
        if not self._push_url:
            logger.warning("[RTMPOutput] 未配置推流地址 (--push_url)")
            return
        logger.info("[RTMPOutput] RTMP 推流已启动 → %s", self._push_url)

    def push_video_frame(self, frame: NDArray[np.uint8]) -> None:
        if not isinstance(frame, np.ndarray):
            return
        if self._ffmpeg_proc is None:
            self._height, self._width = frame.shape[:2]
            cmd = [
                "ffmpeg",
                "-y", "-v", "error",
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "-s", f"{self._width}x{self._height}",
                "-r", str(self._fps),
                "-i", "-",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-pix_fmt", "yuv420p",
                "-f", "flv",
                self._push_url,
            ]
            try:
                self._ffmpeg_proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, shell=False
                )
            except FileNotFoundError:
                logger.error("[RTMPOutput] ffmpeg 未找到，请安装 ffmpeg 并添加到 PATH")
                return
        if self._ffmpeg_proc and self._ffmpeg_proc.stdin:
            try:
                self._ffmpeg_proc.stdin.write(frame.tobytes())
            except BrokenPipeError:
                logger.error("[RTMPOutput] FFmpeg 管道断开")

    def push_audio_frame(self, frame: NDArray[np.int16], eventpoint=None) -> None:
        # RTMP 音频需单独处理，当前版本仅推送视频
        if self.parent is not None:
            self.parent.notify(eventpoint)

    def stop(self) -> None:
        if self._ffmpeg_proc:
            try:
                self._ffmpeg_proc.stdin.close()
            except Exception:
                pass
            self._ffmpeg_proc.wait(timeout=5)
        logger.info("[RTMPOutput] 已停止")
