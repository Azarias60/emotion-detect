# -*-coding: utf-8 -*-
"""API routers for detection and hardware endpoints."""
from fastapi import APIRouter

from .emotion import router as emotion_router
from .health import router as health_router
from .tongue import router as tongue_router
from .fatigue import router as fatigue_router
from .hardware import router as hardware_router
from .companion import router as companion_router
from .digital_human import router as digital_human_router
from .asr import router as asr_router
from .instance import router as instance_router

api_router = APIRouter()
api_router.include_router(emotion_router)
api_router.include_router(health_router)
api_router.include_router(tongue_router)
api_router.include_router(fatigue_router)
api_router.include_router(hardware_router)
api_router.include_router(companion_router)
api_router.include_router(digital_human_router)
api_router.include_router(asr_router)
api_router.include_router(instance_router)

__all__ = [
    "api_router",
    "emotion_router",
    "health_router",
    "fatigue_router",
    "hardware_router",
    "companion_router",
    "digital_human_router",
    "asr_router",
    "instance_router",
]
