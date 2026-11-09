"""后端硬件舌象抓拍接口。"""
import os
import shutil
import tempfile

import cv2
from fastapi import APIRouter, Depends, HTTPException
from core.responses import success

from core.logger import get_logger
from services.dependencies import get_device, get_tongue_analyzer
from tongue_diagnosis.errors import TonguePredictorError

router = APIRouter(prefix="/health", tags=["舌象检测"])
logger = get_logger(__name__)


@router.post("/tongue/capture")
async def capture_and_detect_tongue(
    analyze_tongue_image=Depends(get_tongue_analyzer), device=Depends(get_device)
):
    """从串口摄像头当前帧抓拍并分析舌象。"""
    frame = device.get_latest_frame(copy=True)
    if frame is None:
        raise HTTPException(status_code=409, detail="当前没有可用摄像头画面")

    temp_dir = tempfile.mkdtemp(prefix="tongue_capture_")
    image_path = os.path.join(temp_dir, "capture.jpg")
    try:
        if not cv2.imwrite(image_path, frame):
            raise HTTPException(status_code=500, detail="摄像头画面保存失败")
        data = analyze_tongue_image(image_path)
        return success(data, "舌象检测成功")
    except (ValueError, TonguePredictorError) as error:
        raise HTTPException(status_code=422, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


