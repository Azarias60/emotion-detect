# -*- coding: utf-8 -*-
"""流式 LLM 客户端（搬运自 LiveTalking services/llm_service.py）。

要点（LiveTalking 实测经验，原样保留）：
- 进程级客户端复用：每轮对话新建 OpenAI 客户端会丢连接池，首字延迟里
  白白多出一次 TLS/TCP 握手。客户端线程安全，按 (key, base_url) 缓存。
- 空 key 时 openai 客户端建都建不起来；本地端点给占位符放行，
  真正的校验留到发请求时（_ensure_api_key）。
- 本地端点快速失败：8 秒超时、不重试。
"""

import threading

from .config import LLM_CONFIG, is_local_endpoint
from .llm_memory import strip_leading_tag

_CLIENTS: dict[tuple[str, str], object] = {}
_CLIENTS_LOCK = threading.Lock()


def _shared_client(api_key: str, base_url: str | None):
    cache_key = (api_key or "", base_url or "")
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(cache_key)
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url)
            _CLIENTS[cache_key] = client
        return client


class LLMService:
    def __init__(self, config: dict | None = None):
        self.config = dict(config or LLM_CONFIG)
        base_url = self.config.get("base_url")
        self._local = is_local_endpoint(base_url)
        api_key = self.config.get("api_key") or ""
        if not api_key:
            # 空 key 时给占位符让客户端先建起来，校验留给 _ensure_api_key
            api_key = "local-no-key" if self._local else "missing-api-key"
        self.client = _shared_client(api_key, base_url)

    def _ensure_api_key(self) -> None:
        key = self.config.get("api_key") or ""
        if not key and not self._local:
            raise RuntimeError("LLM_API_KEY 未配置：请设置环境变量 LLM_API_KEY（或改用本地推理端点）")

    def stream_text(self, message: str, system_prompt: str | None = None, memory=None):
        """流式生成回复。

        memory 传 ConversationMemory 时把历史一起发，本轮结束后写回
        "用户这句 + 助手这句"；不传就是无记忆单轮。
        """
        self._ensure_api_key()
        messages = [{"role": "system", "content": system_prompt or self.config.get("system_prompt", "")}]
        if memory is not None:
            messages.extend(memory.messages())
        messages.append({"role": "user", "content": message})

        kwargs = {
            "model": self.config["model"],
            "messages": messages,
            "temperature": self.config.get("temperature", 0.6),
            "max_tokens": self.config.get("max_tokens", 300),
            "stream": True,
            "timeout": self.config.get("local_timeout", 8.0) if self._local else self.config.get("cloud_timeout", 60.0),
        }
        if not self._local and self.config.get("max_retries", 0) == 0:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        response = self.client.chat.completions.create(**kwargs)

        collected: list[str] = []
        try:
            for chunk in response:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    collected.append(delta)
                    yield delta
        finally:
            # 主动关闭 HTTP 流，打断后不占连接池
            try:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
            finally:
                if memory is not None:
                    memory.append("user", message)
                    reply = strip_leading_tag("".join(collected))
                    if reply:
                        memory.append("assistant", reply)
