"""capture 公开入口：装配创建与设备会话发布/释放。

跨模块业务只允许导入本文件与 ``types/``；内核实现、缓冲和会话
细节留在包内，由 presentation/signaling 等外层适配经此访问。
"""
from server.capture.webrtc.jpeg import OpenCvCanonicalFrameDecoder
from server.capture.webrtc.kernel import CaptureKernel, create_kernel
from server.capture.webrtc.session import SessionStateError

__all__ = [
    "CaptureKernel",
    "OpenCvCanonicalFrameDecoder",
    "SessionStateError",
    "create_kernel",
]
