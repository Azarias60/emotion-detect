# -*-coding: utf-8 -*-
"""FastAPI routers for system management, authentication, reports, and analytics."""
from fastapi import APIRouter

from .analytics import router as analytics_router
from .auth import router as auth_router
from .files import router as files_router
from .reports import router as reports_router
from .users import router as users_router

management_router = APIRouter()
management_router.include_router(auth_router)
management_router.include_router(users_router)
management_router.include_router(reports_router)
management_router.include_router(analytics_router)
management_router.include_router(files_router)

__all__ = [
    "management_router",
    "auth_router",
    "users_router",
    "reports_router",
    "analytics_router",
    "files_router",
]
