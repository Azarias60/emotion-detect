"""Errors shared by the detection workflows and their outer adapters.

各错误族以 ``http_status`` 自述对 HTTP 语义（RFC 9110）的选择——这是领域
契约的一部分，与错误文案同级；传输边界用 Starlette 官方扩展点把它们转换
为官方 ``{"detail": ...}`` 形状，不再有任何中间响应构造层。无子类型的
未分类失败固定 500，且公开文案不外泄实现细节。
"""


class DetectionFlowError(Exception):
    """检测流程无法完成（未分类失败）。"""

    http_status = 500

    def __str__(self):
        if type(self) is DetectionFlowError:
            # 未分类失败不外泄内部细节，收敛为固定公开文案。
            return "检测失败，请查看服务端日志"
        return super().__str__()


class DetectionBadRequestError(DetectionFlowError):
    """请求内容无法按声明的语法解析。"""

    http_status = 400


class DetectionInputError(DetectionFlowError):
    """已解析的请求缺少必需数据或不满足分析条件。"""

    http_status = 400


class DetectionBusyError(DetectionFlowError):
    """检测任务占用，当前请求与运行中的任务冲突。"""

    http_status = 409


class DetectionUnavailableError(DetectionFlowError):
    """检测所需的模型或能力尚不可用。"""

    http_status = 503


class DetectionUpstreamError(DetectionFlowError):
    """上游服务失败或返回不符合约定的响应。"""

    http_status = 502


class DetectionTimeoutError(DetectionUpstreamError):
    """等待上游服务响应超时。"""

    http_status = 504


__all__ = [
    "DetectionBusyError",
    "DetectionFlowError",
    "DetectionInputError",
    "DetectionBadRequestError",
    "DetectionUpstreamError",
    "DetectionTimeoutError",
    "DetectionUnavailableError",
]
