"""FastAPI capability routes and public error envelopes.

检测意图路由只提交身份与模式：启动、结束、取消、重试与展示反馈都以
报告标识为目标，归属由认证边界验证；检测执行的资源与使用期归业务
调度，网关不持有任务、时钟或采集记录。
"""

import array
import time

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from server.detection_errors import DetectionFlowError
from server.envelope import Envelope, ErrorResponse, success
from server.logutil import get_logger
from server.presentation.gateway.ports import GatewayPorts
from server.presentation.gateway.schemas import (
    BehaviorResult,
    CaptureStatusPayload,
    DetectionIntent,
    DetectionReportId,
    ExpressionResult,
    QuestionIndex,
    SpeechResult,
    UsageCondition,
)
from server.presentation.gateway.operations.observations import (
    handle_behavior_observation,
    handle_expression_observation,
    handle_speech_observation,
)
from server.support.reporting.types.errors import ReportNotFoundError

logger = get_logger(__name__)

# 状态包络的展示参数：取最后 10 秒音频折成 100 个电平点。
AUDIO_STATUS_WINDOW_SECONDS = 10.0
AUDIO_STATUS_POINTS = 100


def _rms(pcm: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if not samples:
        return 0.0
    return (sum(float(v) * v for v in samples) / len(samples)) ** 0.5


def _audio_level_envelope(window) -> dict:
    """从 ``audio_window(seconds=10)`` 计算前端语音波形电平（网关层职责）。

    窗口右端对齐最近一次音频到达：``levels`` 只覆盖窗口内实际存在的音频
    （RMS 归一化到 [0, 1]，静音期如实呈现低电平，不左补零伪造时间轴）；
    ``age_ms`` 是最新音频距本次调用的时延（驱动前端静音折叠），
    ``duration_ms`` 是窗口内实际音频时长。
    """
    step_ms = int(AUDIO_STATUS_WINDOW_SECONDS * 1000 / AUDIO_STATUS_POINTS)
    if window is None:
        return {"levels": [], "step_ms": step_ms, "age_ms": None, "duration_ms": 0}
    pcm = window.pcm
    age_ms = (None if window.received_at is None
              else max(0, int((time.monotonic() - window.received_at) * 1000)))
    duration_ms = int(len(pcm) / (window.sample_rate * 2) * 1000)
    usable = len(pcm) // 2  # PCM16 样本数
    levels = []
    if usable < AUDIO_STATUS_POINTS:
        # 不足一个显示点：整段算一个真实电平，不补零伪造时间轴。
        levels.append(min(1.0, _rms(pcm) / 32768.0 * 8.0))
    else:
        samples_per_point = usable // AUDIO_STATUS_POINTS
        for index in range(AUDIO_STATUS_POINTS):
            chunk = pcm[index * samples_per_point * 2:(index + 1) * samples_per_point * 2]
            levels.append(min(1.0, _rms(chunk) / 32768.0 * 8.0))
    return {
        "levels": levels, "step_ms": step_ms, "age_ms": age_ms,
        "duration_ms": duration_ms,
    }


# 能力路由的标准错误面（官方 {detail} 形状；校验 422 由框架自动声明）。
CAPABILITY_ERRORS = {
    400: {"model": ErrorResponse, "description": "检测输入不满足执行条件"},
    401: {"model": ErrorResponse, "description": "未登录或登录凭证无效"},
    404: {"model": ErrorResponse, "description": "报告不存在"},
    409: {"model": ErrorResponse, "description": "任务忙（准入拒绝）"},
}


def _errors(*codes: int) -> dict:
    return {code: CAPABILITY_ERRORS[code] for code in codes}


def install_error_handlers(app: FastAPI) -> None:
    """错误直面官方规范。HTTPException 完全走框架默认；检测域错误族经
    Starlette 官方扩展点做唯一的一步转换——状态码由异常类自述（RFC 9110
    语义），响应体即官方 ``{"detail": ...}`` 形状，无任何中间响应构造。
    请求校验保留官方 422 结构化 ``detail`` 数组（loc/msg/type，框架声明
    的契约形状），但不在响应中回显 ``input`` 原始输入——请求体可能携带
    明文密码。未捕获异常交回框架默认 500。"""

    @app.exception_handler(DetectionFlowError)
    async def detection_flow_error(request, exc: DetectionFlowError):
        if exc.http_status >= 500:
            logger.error("检测流程失败", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse(status_code=exc.http_status, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]})


def create_router(ports: GatewayPorts) -> APIRouter:
    """观测与采集状态路由：只读取能力当前事实，不拥有任何执行资源。"""
    router = APIRouter(tags=["能力网关"])

    @router.post("/observations/expression", response_model=Envelope[ExpressionResult],
                 responses=_errors(400, 409))
    async def observe_expression():
        return await handle_expression_observation(
            admission=ports.admission,
            capture=ports.capture,
            expression=ports.expression,
        )

    @router.post("/observations/behavior", response_model=Envelope[BehaviorResult],
                 responses=_errors(400, 409))
    async def observe_behavior():
        return await handle_behavior_observation(
            admission=ports.admission,
            behavior_observation_flow=ports.behavior_observation_flow,
        )

    @router.post("/observations/speech", response_model=Envelope[SpeechResult],
                 responses=_errors(400, 409))
    async def observe_speech():
        return await handle_speech_observation(
            admission=ports.admission,
            capture=ports.capture,
            speech=ports.speech,
        )

    @router.get("/capture/status", response_model=Envelope[CaptureStatusPayload])
    async def capture_status():
        """采集设备与媒体可用状态；冻结的传感器数据不越出 capture。"""
        connected = ports.capture.is_connected()
        data = {
            "connected": connected,
            "frame_available": ports.capture.video_window() is not None,
            "audio": _audio_level_envelope(ports.capture.audio_window(
                AUDIO_STATUS_WINDOW_SECONDS)),
        }
        return success(data, "采集状态")

    return router


def create_detection_router(current_principal, execution, reports) -> APIRouter:
    """检测意图路由：身份来自认证边界，目标由报告标识显式表达。"""
    router = APIRouter(tags=["检测"])

    def owned_report(report_id: int, user):
        """表达意图前验证报告归属；不存在或不属于当前账号一律 404。"""
        try:
            reports.report_detail(user.user_id, report_id)
        except ReportNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/detections", response_model=Envelope[DetectionReportId],
                 responses=_errors(400, 401, 409))
    async def begin_detection(payload: DetectionIntent, user=Depends(current_principal)):
        report_id = await execution.start(user.user_id, payload.kind)
        return success({"reportId": report_id}, "已开始检测")

    @router.post("/detections/{report_id}/finish", response_model=Envelope[None],
                 responses=_errors(400, 401, 404))
    async def finish_detection(report_id: int, user=Depends(current_principal)):
        owned_report(report_id, user)
        await execution.finish(report_id)
        return success(message="已结束采集，分析继续进行")

    @router.post("/detections/{report_id}/cancel", response_model=Envelope[None],
                 responses=_errors(400, 401, 404))
    async def cancel_detection(report_id: int, user=Depends(current_principal)):
        owned_report(report_id, user)
        await execution.cancel(report_id)
        return success(message="已取消本轮检测")

    @router.post("/detections/{report_id}/retry", response_model=Envelope[None],
                 responses=_errors(400, 401, 404))
    async def retry_detection(report_id: int, user=Depends(current_principal)):
        owned_report(report_id, user)
        await execution.retry(report_id)
        return success(message="已重试分析")

    @router.post("/detections/{report_id}/usage", response_model=Envelope[None],
                 responses=_errors(400, 401))
    async def express_usage(report_id: int, payload: UsageCondition,
                            user=Depends(current_principal)):
        """表达检测页面的使用条件；报告已移除时本轮已不存在，静默受理。"""
        try:
            reports.report_detail(user.user_id, report_id)
        except ReportNotFoundError:
            return success(message="本轮检测已结束")
        usage = None if payload.mode is None else {"accountId": user.user_id, "mode": payload.mode}
        await execution.set_usage(report_id, usage)
        return success(message="已更新使用条件")

    @router.post("/detections/{report_id}/questions/displayed", response_model=Envelope[None],
                 responses=_errors(400, 401, 404))
    async def question_displayed(report_id: int, payload: QuestionIndex,
                                 user=Depends(current_principal)):
        owned_report(report_id, user)
        await execution.question_displayed(report_id, payload.index)
        return success(message="已确认题目展示")

    return router


__all__ = ["create_detection_router", "create_router", "install_error_handlers"]
