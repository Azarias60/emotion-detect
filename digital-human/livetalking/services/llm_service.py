import json
import threading
import time
from pathlib import Path

from utils.logger import logger

from services.llm_memory import strip_leading_tag


ALLOWED_ACTIONS = {"greet", "encourage", "praise", "comfort", "idle"}

# 进程级客户端复用：每轮对话新建 OpenAI 客户端会丢掉连接池，首字延迟里
# 白白多出一次 TLS/TCP 握手（实测百毫秒量级）。客户端本身线程安全。
_CLIENTS: dict[tuple[str, str], object] = {}
_CLIENTS_LOCK = threading.Lock()

# 本地/自建推理端点（Ollama、LM Studio、vLLM、llama.cpp server…）通常不需要
# API key，但 openai 客户端要求非空字符串。这些主机名一律给占位符放行。
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "host.docker.internal")


def is_local_endpoint(base_url: str | None) -> bool:
    url = (base_url or "").lower()
    return any(host in url for host in _LOCAL_HOSTS)


def _shared_client(api_key: str, base_url: str | None):
    cache_key = (api_key or "", base_url or "")
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(cache_key)
        if client is None:
            from openai import OpenAI

            # 超时必须显式配：openai 默认 timeout=600s + 重试 2 次，一条死连接
            # （VPN 切换等）会把共享客户端毒化，之后每轮 LLM 都挂住到 10 分钟
            # （2026-10-23 实测"正在思考"卡死）。stream_text 首字正常 <2s，20s 足够。
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=20, max_retries=1)
            _CLIENTS[cache_key] = client
        return client


class LLMService:
    def __init__(self, opt):
        self.opt = opt
        base_url = getattr(opt, "llm_base_url", None)
        self._local = is_local_endpoint(base_url)
        api_key = getattr(opt, "llm_api_key", "") or ""
        if not api_key:
            # 空 key 时 openai 客户端**建都建不起来**（OpenAIError:
            # Missing credentials），那样报错在构造期就炸，提示也难懂。
            # 给个占位符先让客户端建好，真正的校验留给 _ensure_api_key ——
            # 它会在真正要发请求时给出"LLM_API_KEY is not configured"。
            api_key = "local-no-key" if self._local else "missing-api-key"
        self.client = _shared_client(api_key, base_url)

    def stream_text(self, message: str, system_prompt: str | None = None, memory=None):
        """流式生成回复。

        ``memory`` 传一个 ``services.llm_memory.ConversationMemory`` 时会把
        历史一起发过去，并在本轮结束后把"用户这句 + 助手这句"写回历史 ——
        不传就是原来的无记忆单轮行为。
        """
        self._ensure_api_key()
        messages = [
            {"role": "system", "content": system_prompt or self.opt.llm_system_prompt}
        ]
        if memory is not None:
            messages.extend(memory.messages())
        messages.append({"role": "user", "content": message})
        # 连接失败有界重试（2026-11-05）：瞬断（WinError 10061 等）会让整轮
        # 对话直接失败；首 token 尚未吐出前重试是安全的。最多 2 次额外尝试，
        # 退避 2s/4s；非连接类错误（鉴权/参数/内容策略）不重试。
        response = None
        delay = 2.0
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model=self.opt.llm_model,
                    messages=messages,
                    stream=True,
                )
                break
            except Exception as exc:
                name = type(exc).__name__
                retriable = (
                    name in ("APIConnectionError", "ConnectError", "ReadError")
                    or "ConnectError" in str(exc)
                    or "Connection" in str(exc)
                )
                if attempt == 2 or not retriable:
                    raise
                print(f"[LLM] 连接失败（{name}），{delay:.0f}s 后重试 "
                      f"({attempt + 1}/2)", flush=True)
                time.sleep(delay)
                delay *= 2
        collected: list[str] = []
        try:
            for chunk in response:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    collected.append(delta)
                    yield delta
        finally:
            # 主动关闭 HTTP 流，打断后不占用连接池或继续接收旧回复。
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

    def generate_childcare_reply(self, message: str, prompt_text: str) -> dict:
        self._ensure_api_key()
        request_kwargs = {
            "model": self.opt.llm_model,
            "messages": [
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": message},
            ],
            "temperature": 0.6,
        }
        try:
            response = self.client.chat.completions.create(
                response_format={"type": "json_object"},
                **request_kwargs,
            )
        except Exception:
            logger.warning("LLM json_object mode unavailable, falling back to plain text JSON prompt")
            response = self.client.chat.completions.create(**request_kwargs)
        raw_content = response.choices[0].message.content if response.choices else ""
        parsed = self._parse_json_payload(raw_content)
        reply = str(parsed.get("reply", "")).strip()
        action = self._normalize_action(parsed.get("action"))
        if not reply:
            raise ValueError("LLM returned an empty reply")
        return {"reply": reply, "action": action}

    def load_prompt_file(self, prompt_path: str) -> str:
        path = Path(prompt_path)
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return ""

    def _ensure_api_key(self):
        # 本地端点（Ollama 等）不需要 key，放行
        if getattr(self.opt, "llm_api_key", "") or getattr(self, "_local", False):
            return
        raise ValueError("LLM_API_KEY is not configured")

    def _parse_json_payload(self, raw_content: str) -> dict:
        raw_content = (raw_content or "").strip()
        if not raw_content:
            return {}
        try:
            return json.loads(raw_content)
        except json.JSONDecodeError:
            pass

        start = raw_content.find("{")
        end = raw_content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw_content[start : end + 1])
            except json.JSONDecodeError:
                logger.warning("Failed to parse structured LLM payload: %s", raw_content)
        return {}

    def _normalize_action(self, action_value) -> str:
        action = str(action_value or "idle").strip().lower()
        if action not in ALLOWED_ACTIONS:
            return "idle"
        return action
