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
from core.state import current_detection, detection_global_lock
from services.detection_service import ModelUnavailableError
from services.media_service import write_recorded_video

logger = get_logger(__name__)


@asynccontextmanager
async def detection_session(
    module_name: str,
    temp_prefix: str,
    mcu_video_name: str,
    error_detail: str,
    *,
    device=None,
):
    """检测会话生命周期（2026-11-11 起三模块全局互斥）。

    先获取全局检测锁（任一其他模块分析中即抛 409，不改变采集状态），再停止
    MCU 采集、创建临时目录并解析视频源，yield (temp_dir, video_path)；
    退出时清理临时目录并释放锁，未预期异常统一映射为 500。
    采集不到画面时抛 409（不再放行空数据会话，2026-11-13）。
    """
    holder = current_detection.get("module")
    acquired = detection_global_lock.acquire(blocking=False)
    if not acquired:
        # acquire 失败后重新读一次：finally 里 release 与清 module 相邻执行，
        # 抢锁失败的瞬间可能读到 None，文案不能出现「None 正在进行中」
        holder = current_detection.get("module") or "其他检测"
        raise HTTPException(
            status_code=409,
            detail=f"「{holder}」正在进行中，请等它完成或先停止，再开始「{module_name}」",
        )
    current_detection["module"] = module_name

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
        if video_path is None:
            # 没有采集到任何画面：单片机没连/没开采集/摄像头没数据，一律明确拒绝。
            # 以前这里会带着空数据走完全部分析，返回"检测成功"+空指标，
            # 用户看到的是一份什么都没有的报告（2026-11-13 修复）。
            connected = bool(getattr(device, "is_connected", False))
            raise HTTPException(
                status_code=409,
                detail=(
                    "没有采集到摄像头画面——请先确认左上角硬件状态为「已连接」，"
                    "然后点击开始检测并保持面部在取景框内"
                    if not connected
                    else "本次没有采集到画面——请先开始检测，录制几分钟后再分析"
                ),
            )
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
        if acquired:
            # 只有本协程真的拿到了锁才释放：提前 raise 的路径（锁都没拿到）
            # 不能去 release，否则 RuntimeError 把 409 变成 500
            current_detection["module"] = None
            detection_global_lock.release()


def detection_status() -> dict:
    """全局检测状态（前端状态条用）。"""
    module = current_detection.get("module")
    return {"busy": bool(module), "module": module}


def resolve_heart_rate(video_path) -> dict:
    """旧版入口兼容；新业务通过注入的 DetectionService 调用。"""
    from services.dependencies import get_detection_service
    return get_detection_service().heart_rate(video_path)
