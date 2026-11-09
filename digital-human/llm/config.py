# -*- coding: utf-8 -*-
"""LLM 配置（搬运自 LiveTalking config.py 的 LLM 段，默认 DeepSeek）。

优先级：环境变量 > 默认值。本地推理端点（Ollama/LM Studio/llama.cpp server
等，含本机 1919）不需要 API key，自动放行。
"""

import os

# LiveTalking config.py 里的默认 key（DeepSeek）。可用环境变量覆盖：
#   LLM_API_KEY / DASHSCOPE_API_KEY
DEFAULT_LLM_API_KEY = "sk-0bdafe8bc06740c6904b1032578f570d"

LLM_CONFIG = {
    "base_url": os.getenv("LLM_BASE_URL", os.getenv("LOCAL_LLM_BASE_URL", "https://api.deepseek.com/v1")),
    "api_key": os.getenv("LLM_API_KEY", os.getenv("DASHSCOPE_API_KEY", os.getenv("LOCAL_LLM_API_KEY", DEFAULT_LLM_API_KEY))),
    "model": os.getenv("LLM_MODEL", os.getenv("LOCAL_LLM_MODEL", "deepseek-chat")),
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.6")),
    "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "300")),
    # 本地端点快速失败：连不上时 8 秒超时、不重试，别把首字延迟拖到十几秒
    "local_timeout": float(os.getenv("LLM_LOCAL_TIMEOUT", "8.0")),
    "cloud_timeout": float(os.getenv("LLM_TIMEOUT", "60.0")),
    "max_retries": int(os.getenv("LLM_MAX_RETRIES", "0")),
}

# 本地/自建推理端点主机名（这些不需要真 key）
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "host.docker.internal")


def is_local_endpoint(base_url: str | None) -> bool:
    url = (base_url or "").lower()
    return any(host in url for host in _LOCAL_HOSTS)
