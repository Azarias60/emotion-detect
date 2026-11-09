# -*-coding: utf-8 -*-
"""HTTP 检测会话适配：分析锁、采集收尾、视频源解析、临时目录与异常映射。

业务编排和心率融合位于 detection_service；本模块保留旧版心率入口供兼容。
"""
import shutil
import tempfile
from contextlib import asynccontextmanager

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from core.logger import get_logger
from services.detection_service import ModelUnavailableError
from services.media_service import write_recorded_video

logger = get_logger(__name__)


@asynccontextmanager
async def detection_session(
    lock,
    temp_prefix: str,
    mcu_video_name: str,
    busy_detail: str,
    error_detail: str,
    *,
    device=None,
):
    """检测会话生命周期。

    先获取分析锁（占用时抛 409，不改变采集状态），再停止 MCU 采集、
    创建临时目录并解析视频源，yield (temp_dir, video_path)；
    退出时清理临时目录并释放锁，未预期异常统一映射为 500。
    video_path 为 None 表示本次会话没有任何视频源。
    """
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=busy_detail)

    temp_dir = None
    try:
        if device is None:
            from mcu import mcu_client
            device = mcu_client
        if device.is_recording:
            logger.info("检测到采集正在运行，锁定数据")
            device.stop_recording()
        temp_dir = tempfile.mkdtemp(prefix=temp_prefix)
        video_path = await run_in_threadpool(write_recorded_video, temp_dir, mcu_video_name, device=device)
        yield temp_dir, video_path
    except ModelUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except HTTPException:
        raise
    except Exception as error:
        logger.error("%s：%s", error_detail, error, exc_info=True)
        raise HTTPException(status_code=500, detail=f"{error_detail}：{error}")
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
        lock.release()


def resolve_heart_rate(video_path) -> dict:
    """旧版入口兼容；新业务通过注入的 DetectionService 调用。"""
    from services.dependencies import get_detection_service
    return get_detection_service().heart_rate(video_path)
