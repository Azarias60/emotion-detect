# -*-coding: utf-8 -*-
"""全局统一日志：业务模块一律经 get_logger() 获取 logger，替代散落的 print。"""
import logging
import sys

_CONFIGURED = False
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _configure() -> None:
    """幂等配置：在 emotion 命名空间根上挂一次 handler，与 uvicorn 日志互不干扰。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
    root = logging.getLogger("emotion")
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """取业务 logger，name 一般传 __name__（如 routers.emotion）。"""
    _configure()
    return logging.getLogger(f"emotion.{name}")
