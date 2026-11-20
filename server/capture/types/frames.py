"""规范帧只读读取与订阅契约；跨模块仅经本契约访问采集帧数据。"""

from typing import Protocol

from server.capture.domain import VideoWindow


class VideoFrameSubscription(Protocol):
    """有界最新 JPEG 帧订阅：presentation/preview 等只读消费者的帧来源。

    - ``next_frame`` 返回 ``(jpeg_bytes, captured_at, sequence)``；等待超时或
      订阅终止后返回 ``None``。``jpeg_bytes`` 是最新到达且重组完整的原始
      JPEG 字节，解码与方向归一化由订阅方自行按需执行；``captured_at`` 为
      设备拍摄时间，``sequence`` 单调递增。
    - 最新帧满 2 秒未更新时不交付旧帧，等待新帧或超时。
    - 慢订阅者只取最新帧，不积压；设备会话代际替换或内核关闭会终止订阅，
      终止后 ``next_frame`` 恒为 ``None``，调用方应释放并重新协商。
    - 读取方结束时必须调用 ``close``；关闭幂等。
    """

    async def next_frame(self, timeout: float) -> tuple[bytes, float, int] | None: ...

    def close(self) -> None: ...


class CanonicalFrameSource(Protocol):
    """规范帧只读读取与订阅入口；由采集内核实现，预览适配消费。"""

    def is_connected(self) -> bool: ...

    def video_window(self, seconds: float | None = None) -> VideoWindow | None: ...

    def subscribe_video(self) -> VideoFrameSubscription: ...


__all__ = ["CanonicalFrameSource", "VideoFrameSubscription"]
