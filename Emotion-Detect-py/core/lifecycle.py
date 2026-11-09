"""应用生命周期：独立加载可选模型、初始化数据库、启动和关闭设备。"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.config import BASE_DIR
from core.logger import get_logger
from core.state import models
from management.database import init_database
from mcu import mcu_client

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A. 加载人脸表情识别模型
    try:
        from face_detection.face_detection_logic import create_predictor

        models.predictor, _ = create_predictor()
        logger.info("表情检测模型加载完成")
    except Exception as error:
        models.predictor = None
        logger.error("表情检测模型未加载：%s", error)

    # B. 加载语音转写与情感识别模型
    try:
        from funasr import AutoModel

        models.voice_model = AutoModel(
            model=os.path.join(BASE_DIR, "voice_detection", "models", "iic", "SenseVoiceSmall"),
            vad_model=os.path.join(
                BASE_DIR, "voice_detection", "models", "iic",
                "speech_fsmn_vad_zh-cn-16k-common-pytorch",
            ),
            device="cuda:0",
            # 本机 torch(CUDA) 与 onnxruntime 同进程会段错误，改用 PyTorch 推理后端
            use_onnx=False,
        )
        logger.info("语音识别模型加载完成")
    except Exception as error:
        models.voice_model = None
        logger.error("语音识别模型未加载：%s", error)

    # C. 加载舌象四维预测模型
    try:
        from tongue_diagnosis.efficient_net.predictor import TonguePredictor

        models.tongue_predictor = TonguePredictor(device=None)
        logger.info("舌象四维检测模型加载完成")
    except Exception as error:
        models.tongue_predictor = None
        logger.error("舌象模型未加载：%s", error)

    # D. 初始化管理端 SQLite 数据库
    try:
        init_database()
        logger.info("管理端数据库初始化完成")
    except Exception as error:
        logger.error("管理端数据库初始化失败：%s", error)

    # E. 启动单片机串口全模态后台监听
    try:
        mcu_client.start()
        logger.info("单片机串口监听服务已启动")
    except Exception as error:
        logger.error("单片机监听服务启动失败：%s", error)

    # F. 拉起数字人引擎（digital-human/livetalking，8020）——情感数字人页面
    #    的 WebRTC 后端。引擎是应用自己的组件，生命周期随应用：静默启动
    #    （绝不弹浏览器）、退出时回收；DETECT_DH_ENGINE=0 可关闭。
    if os.environ.get("DETECT_DH_ENGINE", "1") != "0":
        try:
            from core.dh_engine import start_engine

            start_engine()
        except Exception as error:
            logger.error("数字人引擎随应用启动失败：%s", error)
    else:
        logger.info("DETECT_DH_ENGINE=0，数字人引擎不随应用启动")

    try:
        yield
    finally:
        # 即使应用生命周期异常退出，也要停止硬件监听。
        mcu_client.stop()
        try:
            from core.dh_engine import stop_engine

            stop_engine()
        except Exception:
            pass
        logger.info("服务已安全关闭")
