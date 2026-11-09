"""硬件 HTTP 适配；设备操作和 MJPEG 编码交给硬件服务。"""
from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from core.contracts import CaptureRequest, TransportRequest
from core.responses import success
from services.dependencies import get_hardware_service
from services.hardware_service import HardwareService

router = APIRouter(tags=["单片机与硬件交互"])


@router.get("/video_feed")
async def video_feed(request: Request, service: HardwareService = Depends(get_hardware_service)):
    return StreamingResponse(service.frames(request.is_disconnected), media_type="multipart/x-mixed-replace; boundary=frame")


@router.post("/serial/start")
@router.post("/hardware/start")
async def api_start_hardware(
    payload: CaptureRequest | None = Body(None),
    record_duration: int = Query(60, ge=1, le=600),
    capture_video: bool = True,
    service: HardwareService = Depends(get_hardware_service),
):
    # 保留旧版 query 调用，JSON 中未指定的字段继续使用 query 值。
    settings = {"record_duration": record_duration, "capture_video": capture_video}
    if payload is not None:
        settings.update(payload.model_dump(exclude_unset=True))
    service.start(settings["record_duration"], settings["capture_video"])
    return success(msg=f"单片机多模态采集已启动，计划录制 {settings['record_duration']} 秒")


@router.post("/serial/stop")
@router.post("/hardware/stop")
async def api_stop_hardware(service: HardwareService = Depends(get_hardware_service)):
    return success(service.stop(), "单片机数据采集录制已停止")


@router.get("/serial/data")
@router.get("/hardware/data")
async def api_get_hardware_data(service: HardwareService = Depends(get_hardware_service)):
    return success(service.status(), "success")


@router.get("/hardware/mode")
@router.get("/serial/mode")
async def api_get_transport_mode(service: HardwareService = Depends(get_hardware_service)):
    return success(service.transport(), "success")


@router.post("/hardware/mode")
@router.post("/serial/mode")
async def api_set_transport_mode(payload: TransportRequest, service: HardwareService = Depends(get_hardware_service)):
    if payload.mode not in {"wifi", "serial"}:
        return JSONResponse(status_code=400, content={
            "code": 400,
            "msg": f"不支持的传输模式: '{payload.mode}'，仅支持 'wifi' 或 'serial'",
        })
    label = "WiFi (TCP+UDP)" if payload.mode == "wifi" else "串口 (Serial)"
    return success(service.switch(payload), f"已配置 {label} 连接，等待固件确认输出模式")
