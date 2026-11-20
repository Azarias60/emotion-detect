# -*-coding: utf-8 -*-
"""应用装配：lifespan、CORS、统一异常封装与路由挂载。

顶层路由仅保留设备入流 WHIP 端点 ``POST /whip/capture``（RFC 9725）；设备
预览通过 ``/api/v1/capture/preview/offer`` 建立服务端下行 WebRTC，业务能力
统一挂在 ``/api/v1``，不暴露 MJPEG 或轮询画面端点。路由与生命周期消费
``bootstrap.assemble`` 的装配结果；请求处理器不回读 app.state 取服务。
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from server import settings
from server.bootstrap import assemble
from server.capture.service import OpenCvCanonicalFrameDecoder
from server.presentation.signaling.whip import create_signaling_router
from server.presentation.preview import create_preview_router
from server.support.identity.http import create_identity_router
from server.support.reporting.http import create_reporting_router
from server.logutil import get_logger

logger = get_logger("app")


def _build_lifespan(assembly, preview_manager, upload_root):
    models = (
        ("expression", assembly.expression),
        ("speech", assembly.speech),
        ("tongue", assembly.tongue),
        ("behavior", assembly.behavior),
        ("facephys", assembly.facephys),
    )
    # --- 生命周期：任何必需模型或数据库加载失败均终止启动，finally 释放已创建资源 ---
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            # PHYSIOSENSE_ALLOW_MISSING_MODELS=1：缺模型（如本机没装 dlib）降级启动，
            # 相关接口按需报错而不是让整个服务起不来（本地联调友好，默认行为不变）
            import os as _os
            allow_missing = _os.getenv("PHYSIOSENSE_ALLOW_MISSING_MODELS") == "1"
            failed_models: list[str] = []
            for name, resource in models:
                try:
                    resource.load()
                    logger.info("%s 模型加载完成", name)
                except Exception as error:
                    if not allow_missing:
                        raise
                    failed_models.append(name)
                    logger.error(
                        "%s 模型加载失败（允许缺失，降级启动）：%s", name, error,
                    )
            if failed_models:
                logger.warning(
                    "降级启动：以下能力不可用，相关接口将按需报错：%s",
                    ", ".join(failed_models),
                )
            assembly.init_identity_database()
            assembly.init_reporting_database()
            upload_root.mkdir(parents=True, exist_ok=True)
            logger.info("管理端数据库初始化完成")
            yield
        finally:
            for name, resource in models:
                try:
                    resource.close()
                    logger.info("%s 模型资源已释放", name)
                except Exception as error:
                    logger.error("关闭 %s 模型异常：%s", name, error, exc_info=True)

            try:
                await preview_manager.close()
                logger.info("预览管理器已关闭")
            except Exception as error:
                logger.error("关闭预览管理器异常：%s", error, exc_info=True)

            try:
                await assembly.kernel.close()
                logger.info("采集内核与设备连接已关闭")
            except Exception as error:
                logger.error("关闭采集内核异常：%s", error, exc_info=True)

            logger.info("服务关闭")

    return lifespan


def create_app(assembly=None) -> FastAPI:
    assembly = assembly if assembly is not None else assemble()
    upload_root = Path(settings.UPLOAD_DIR)
    from server.presentation.conversation import create_conversation_router
    from server.presentation.gateway.routes import (
        create_detection_router, create_router, install_error_handlers,
    )
    from server.presentation.observation import create_observation_router
    from server.presentation.production import create_production_router
    from server.presentation.resources import (
        create_model_config_router, create_objects_router,
    )

    preview_router, preview_manager = create_preview_router(
        assembly.kernel, OpenCvCanonicalFrameDecoder(),
        principal=assembly.identity.current_principal,
    )
    app = FastAPI(
        title="PhysioSense Server", version="2.0",
        lifespan=_build_lifespan(assembly, preview_manager, upload_root),
    )
    # CORS 来源由部署配置显式声明（生产收紧为前端来源列表，默认 * 保持
    # 本地调试契约）；凭据模式下 Access-Control-Allow-Origin 禁止为 *，
    # 前端 token 走请求头，不依赖 cookie，无需 CSRF 令牌。
    app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ALLOW_ORIGINS,
                       allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

    install_error_handlers(app)

    @app.get("/healthz")
    def healthz():
        return {"code": 0, "message": "成功", "data": {"status": "ok"}}

    # 设备信令端点：单片机固化目标（经反向代理映射到本进程）。
    app.include_router(create_signaling_router(assembly.kernel))

    router = APIRouter(prefix="/api/v1")
    router.include_router(create_reporting_router(
        assembly.identity.current_principal, assembly.reporting,
    ))
    router.include_router(create_detection_router(
        assembly.identity.current_principal, assembly.detection, assembly.reporting,
    ))
    router.include_router(create_observation_router(
        assembly.identity.current_principal, assembly.reporting,
    ))
    router.include_router(create_router(assembly.ports))
    router.include_router(create_objects_router(
        assembly.identity.current_principal, assembly.objects,
    ))
    router.include_router(create_model_config_router(
        assembly.identity.current_principal, assembly.model_config,
    ))
    router.include_router(create_production_router(
        assembly.identity.current_principal, assembly.productions,
        assembly.recording_port,
    ))
    router.include_router(create_conversation_router(
        assembly.identity.current_principal, assembly.contexts,
    ))
    router.include_router(preview_router)
    router.include_router(create_identity_router(assembly.identity, upload_root))
    app.include_router(router)

    # 静态文件（头像上传）
    app.mount("/uploads", StaticFiles(directory=upload_root, check_dir=False), name="uploads")

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.app:create_app", factory=True, host="0.0.0.0", port=8000)
