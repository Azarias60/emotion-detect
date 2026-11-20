"""Identity HTTP 入口：由装配根注入认证依赖后构建路由。"""
from pathlib import Path

from fastapi import APIRouter

from server.support.identity.http_auth import create_auth_router
from server.support.identity.http_dependencies import IdentityDependencies
from server.support.identity.http_users import create_users_router


def create_identity_router(deps: IdentityDependencies, upload_root: Path) -> APIRouter:
    router = APIRouter()
    router.include_router(create_auth_router(deps))
    router.include_router(create_users_router(deps, upload_root))
    return router
