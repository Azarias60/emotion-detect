# -*-coding: utf-8 -*-
"""情绪检测与心理问题路由。"""
import time

from fastapi import APIRouter, Depends
from core.responses import success

from core.logger import get_logger
from core.state import emotion_analysis_lock
from services.ai_service import generate_psychology_questions
from services.analysis_pipeline import detection_session
from services.dependencies import get_detection_service
from services.detection_service import DetectionService
from routers.dependencies import require_hardware_capture

router = APIRouter(tags=["情绪检测"])

logger = get_logger(__name__)


@router.post("/emotion/detect", dependencies=[Depends(require_hardware_capture)])
@router.post("/report/detect", dependencies=[Depends(require_hardware_capture)])
async def detect_emotion(
    service: DetectionService = Depends(get_detection_service),
):
    start_time_all = time.time()
    logger.info("[情绪识别][START] 收到一分钟多模态分析请求")

    # 1. 视频源：只使用后端单片机录制（含分析锁与临时目录生命周期）
    async with detection_session(
        emotion_analysis_lock,
        temp_prefix="emotion_session_",
        mcu_video_name="mcu_recorded.mp4",
        busy_detail="情绪检测任务正在分析，请勿重复提交",
        error_detail="检测失败",
        device=service.device,
    ) as (temp_dir, video_path):
        data = await service.emotion(temp_dir, video_path)
        logger.info("[情绪识别][DONE] 全部分析完成，总耗时=%.2fs", time.time() - start_time_all)
        return success(data, "检测成功")


@router.get("/emotion/questions")
@router.get("/report/questions")
def get_ai_questions():
    """获取或生成心理健康咨询参考问题列表。"""
    questions = generate_psychology_questions()
    return success(questions, "成功")
