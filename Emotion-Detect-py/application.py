"""FastAPI 应用装配：中间件、路由与静态资源。"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.lifecycle import lifespan as default_lifespan
from management.config import UPLOAD_DIR
from management.routers import management_router
from routers import api_router


def create_app(*, lifespan=default_lifespan) -> FastAPI:
    app = FastAPI(
        title="CUG 多模态健康与情绪智能检测 API",
        description="支持面部表情、语音情感、KDE-HRV、生理疲劳、舌象诊断与综合健康报告的统一服务接口",
        version="2.0.0",
        lifespan=lifespan,
    )

    # 跨域中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 业务与管理路由
    app.include_router(api_router)
    app.include_router(management_router)

    # 静态文件（头像上传）
    if os.path.exists(UPLOAD_DIR):
        app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
    return app
