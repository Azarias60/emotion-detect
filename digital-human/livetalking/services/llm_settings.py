"""LLM 运行时配置：页面可改，key 只存本机文件，代码里不留任何密钥。

来源优先级（高 → 低）：
  1. ``data/llm_config.json``——页面「模型 Key」写入，本机文件，
     已被 ``digital-human/.gitignore`` 挡住，永不入库；
  2. 命令行 / 环境变量（``config.py --llm_api_key / --llm_base_url /
     --llm_model``，默认读 ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``）；
  3. ``config.py`` 的占位值（不含真实密钥）。

热生效的原理：``LLMService`` 每轮对话都会新建，构造时调 :func:`effective`，
所以页面保存后**下一句对话就换用新配置**——不用重启引擎，也不用断开正在
进行的会话。共享客户端缓存按 ``(api_key, base_url)`` 建键，换 key 自然是
一个新客户端，旧的不再被复用。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from utils.logger import logger

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "llm_config.json"

# 本地/自建推理端点（Ollama、LM Studio、vLLM、llama.cpp server…）通常不需要
# API key，但 openai 客户端要求非空字符串。这些主机名一律给占位符放行。
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "host.docker.internal")

# 页面提交时的最短 key：太短必然是粘贴错误，早点挡住比让引擎报鉴权失败清楚
MIN_KEY_LEN = 8

_lock = threading.RLock()
_opt = None
_cached: dict = {}


def is_local_endpoint(base_url: str | None) -> bool:
    url = (base_url or "").lower()
    return any(host in url for host in _LOCAL_HOSTS)


def _clean(value) -> str:
    return str(value or "").strip()


def _load() -> dict:
    """读本机配置；文件不存在 = 页面还没配过（合法状态，不是错误）。"""
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # 损坏的 JSON 不能把引擎带崩
        logger.warning("[LLMSettings] 读取 %s 失败，按未配置处理: %s", CONFIG_PATH, exc)
        return {}


def init(opt=None) -> None:
    """启动时调用一次：记住命令行配置，加载本机文件配置。"""
    global _opt, _cached
    with _lock:
        _opt = opt
        _cached = _load()
    key, base_url, model = effective(opt)
    logger.info(
        "[LLMSettings] 模型配置: base_url=%s model=%s key=%s source=%s",
        base_url or "(空)", model or "(空)", mask(key) or "(未配置)", source(),
    )


def _opt_value(name: str) -> str:
    opt = _opt
    return _clean(getattr(opt, name, "")) if opt is not None else ""


def effective(opt=None) -> tuple[str, str, str]:
    """返回生效的 ``(api_key, base_url, model)``。每轮对话都会调用。"""
    with _lock:
        cfg = dict(_cached)
    if opt is not None:
        key = _clean(cfg.get("api_key")) or _clean(getattr(opt, "llm_api_key", ""))
        base_url = _clean(cfg.get("base_url")) or _clean(getattr(opt, "llm_base_url", ""))
        model = _clean(cfg.get("model")) or _clean(getattr(opt, "llm_model", ""))
        return key, base_url, model
    return (
        _clean(cfg.get("api_key")) or _opt_value("llm_api_key"),
        _clean(cfg.get("base_url")) or _opt_value("llm_base_url"),
        _clean(cfg.get("model")) or _opt_value("llm_model"),
    )


def mask(key: str) -> str:
    """只露头尾：页面上永远看不到完整 key。"""
    text = _clean(key)
    if not text:
        return ""
    if len(text) <= 12:
        return "已配置"
    return f"{text[:3]}…{text[-4:]}"


def source() -> str:
    """key 从哪来：page=页面配置 / local=命令行或环境变量 / unset=没有。"""
    key, _, _ = effective()
    with _lock:
        from_page = bool(_clean(_cached.get("api_key")))
    if not key:
        return "unset"
    return "page" if from_page else "local"


def snapshot() -> dict:
    """给页面的视图：不含明文 key，只给掩码与来源。"""
    key, base_url, model = effective()
    with _lock:
        cfg = dict(_cached)
    return {
        "baseUrl": base_url,
        "model": model,
        "hasKey": bool(key),
        "keyHint": mask(key),
        # 该字段是否被页面配置覆盖（页面清空后回落命令行值）
        "overrides": sorted(k for k in ("base_url", "model", "api_key") if _clean(cfg.get(k))),
        "source": source(),
        "localEndpoint": is_local_endpoint(base_url),
    }


def _write(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    # 只留非空字段：空字符串表示"没覆盖"，不落盘
    data = {k: _clean(v) for k, v in cfg.items() if _clean(v)}
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)  # 仅当前用户可读（Windows 上是 no-op，Linux 上生效）
    except OSError:
        pass
    os.replace(tmp, CONFIG_PATH)


def update(api_key=None, base_url=None, model=None, clear_key: bool = False) -> dict:
    """保存页面配置。

    语义（前端据此实现"留空=不改"）：
      * ``api_key``：None/空串 = 保持不变；``clear_key=True`` 才删除；
      * ``base_url`` / ``model``：None = 保持不变；空串 = 清除覆盖、回落命令行值。
    """
    global _cached
    with _lock:
        cfg = dict(_cached)
        if clear_key:
            cfg.pop("api_key", None)
        elif api_key is not None:
            key = _clean(api_key)
            if key:
                if len(key) < MIN_KEY_LEN:
                    raise ValueError(f"API Key 看起来太短（至少 {MIN_KEY_LEN} 位），请检查是否粘贴完整")
                cfg["api_key"] = key
        if base_url is not None:
            url = _clean(base_url)
            if url and not url.startswith(("http://", "https://")):
                raise ValueError("服务地址必须以 http:// 或 https:// 开头")
            cfg["base_url"] = url
        if model is not None:
            cfg["model"] = _clean(model)
        _write(cfg)
        _cached = {k: v for k, v in cfg.items() if _clean(v)}
    key, _, _ = effective()
    logger.info("[LLMSettings] 页面已更新模型配置: key=%s source=%s", mask(key) or "(未配置)", source())
    return snapshot()


def probe() -> dict:
    """用当前生效配置发一条 1-token 请求，验证 key / 地址 / 模型是否可用。"""
    key, base_url, model = effective()
    if not key and not is_local_endpoint(base_url):
        return {"ok": False, "message": "还没有配置 API Key"}
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=key or "local-no-key",
            base_url=base_url or None,
            timeout=20,
            max_retries=0,
        )
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        return {"ok": True, "message": f"连接正常（{model}）"}
    except Exception as exc:
        text = str(exc) or exc.__class__.__name__
        return {"ok": False, "message": text[:300]}
