"""检测 HTTP 输入边界：只允许后端设备会话。"""
from fastapi import Form, HTTPException, Request
from core.contracts import CaptureSource


async def require_hardware_capture(request: Request, capture_source: CaptureSource = Form("hardware")):
    form = await request.form()
    if "audio" in form or "video" in form:
        raise HTTPException(status_code=400, detail="仅支持后端硬件采集，不接收上传的音视频")
