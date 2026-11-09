# -*-coding: utf-8 -*-
"""健康问诊与舌象检测路由。"""
import json

from fastapi import APIRouter, Depends, Form, HTTPException
from core.responses import success

from core.contracts import ConsultationInput
from pydantic import ValidationError
from core.logger import get_logger
from core.state import health_analysis_lock
from services.ai_service import generate_health_questions
from services.analysis_pipeline import detection_session
from services.dependencies import get_detection_service
from services.detection_service import DetectionService
from routers.dependencies import require_hardware_capture

router = APIRouter(prefix="/health", tags=["健康问诊"])

logger = get_logger(__name__)


@router.post("/questions")
async def health_questions(payload: dict):
    """根据舌象特征动态生成问诊建议问题。"""
    tongue_data = payload.get("tongue", payload)
    return success(generate_health_questions(tongue_data), "问诊问题生成成功")


@router.post("/detect", dependencies=[Depends(require_hardware_capture)])
async def detect_health_session(
    tongue_result: str = Form("{}"),
    questions: str = Form("[]"),
    answers: str = Form("[]"),
    service: DetectionService = Depends(get_detection_service),
):
    """分析舌象关联的一分钟综合健康问诊会话。"""
    try:
        tongue_data = json.loads(tongue_result or "{}")
        question_list = json.loads(questions or "[]")
        answer_list = json.loads(answers or "[]")
        inputs = ConsultationInput(tongue=tongue_data, questions=question_list, answers=answer_list)
    except (json.JSONDecodeError, ValidationError) as error:
        raise HTTPException(status_code=400, detail=f"问诊数据格式错误：{error}")

    logger.info("[健康问诊][START] 开始舌象关联的一分钟问诊分析")
    async with detection_session(
        health_analysis_lock,
        temp_prefix="health_session_",
        mcu_video_name="health_recorded.mp4",
        busy_detail="健康问诊任务正在分析，请勿重复提交",
        error_detail="健康问诊分析失败",
        device=service.device,
    ) as (temp_dir, video_path):
        data = await service.health(temp_dir, video_path, inputs.tongue, inputs.questions, inputs.answers)
        logger.info("[健康问诊][DONE] 健康综合报告生成完成")
        return success(data, "健康问诊分析成功")
