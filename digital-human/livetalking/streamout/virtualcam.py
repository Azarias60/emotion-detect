###############################################################################
#  虚拟摄像头输出模块 (Windows: OBS Virtual Camera; Linux: v4l2loopback)
#  需要安装 pyvirtualcam: pip install pyvirtualcam
###############################################################################

import time
from numpy.typing import NDArray
import numpy as np

from registry import register
from streamout.base_output import BaseOutput
from utils.logger import logger


@register("streamout", "virtualcam")
class VirtualCamOutput(BaseOutput):
    """虚拟摄像头输出：将视频帧作为系统摄像头设备输出。"""

    def __init__(self, opt=None, parent=None, **kwargs):
        super().__init__(opt, parent)
        self._cam = None
        self._fps = getattr(opt, "fps", 25)
        self._frame_interval = 1.0 / self._fps
        self._last_frame_time = 0.0

    def start(self) -> None:
        try:
            import pyvirtualcam
            self._cam = pyvirtualcam.Camera(
                width=self.parent.width if self.parent and self.parent.width else 1920,
                height=self.parent.height if self.parent and self.parent.height else 1080,
                fps=self._fps,
                fmt=pyvirtualcam.PixelFormat.BGR,
            )
            logger.info(
                "[VirtualCam] 虚拟摄像头已启动: %dx%d @ %dfps",
                self._cam.width, self._cam.height, self._fps,
            )
        except ImportError:
            logger.error("[VirtualCam] 请安装 pyvirtualcam: pip install pyvirtualcam")
        except Exception as exc:
            logger.error("[VirtualCam] 启动失败: %s", exc)

    def push_video_frame(self, frame: NDArray[np.uint8]) -> None:
        if self._cam is None or not isinstance(frame, np.ndarray):
            return
        now = time.perf_counter()
        elapsed = now - self._last_frame_time
        sleep_time = self._frame_interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)
        self._last_frame_time = time.perf_counter()
        self._cam.send(frame)

    def push_audio_frame(self, frame: NDArray[np.int16], eventpoint=None) -> None:
        if self.parent is not None:
            self.parent.notify(eventpoint)

    def stop(self) -> None:
        if self._cam:
            self._cam.close()
        logger.info("[VirtualCam] 已停止")
