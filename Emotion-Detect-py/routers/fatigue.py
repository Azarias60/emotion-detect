# -*-coding: utf-8 -*-
"""疲劳检测路由模块。"""
from fastapi import APIRouter, Depends, HTTPException
from core.responses import success

from core.logger import get_logger
from core.state import fatigue_analysis_lock
from services.analysis_pipeline import detection_session
from services.dependencies import get_detection_service
from services.detection_service import DetectionService
from routers.dependencies import require_hardware_capture

router = APIRouter(prefix="/fatigue", tags=["疲劳检测"])

logger = get_logger(__name__)


@router.post("/detect", dependencies=[Depends(require_hardware_capture)])
async def detect_fatigue(
    service: DetectionService = Depends(get_detection_service),
):
    """分析一分钟视频中的疲劳行为特征与 HRV 状态（优先单片机采集数据）。"""
    if not service.device.has_recorded_video():
        raise HTTPException(status_code=409, detail="暂无已采集的视频画面，请先完成检测")

    logger.info("[疲劳检测][START] 开始分析一分钟检测视频")
    async with detection_session(
        fatigue_analysis_lock,
        temp_prefix="fatigue_",
        mcu_video_name="fatigue_mcu.mp4",
        busy_detail="疲劳检测任务正在分析，请勿重复提交",
        error_detail="疲劳检测失败",
        device=service.device,
    ) as (_, video_path):
        data = service.fatigue(video_path)
        logger.info("[疲劳检测][DONE] 疲劳检测结果已返回")
        return success(data, "疲劳检测成功")
