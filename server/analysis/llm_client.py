# -*-coding: utf-8 -*-
"""无业务语义的 LLM 基础通信设施：OpenAI 客户端构造与 AI_CONFIG 绑定。

分析层各能力（情绪报告、健康报告、问诊问题）在自己的 reporting/questions
模块中组合 Prompt 与失败语义；契约测试在本模块的 ``OpenAI`` 属性上注入替身。
"""
from contextlib import contextmanager

from openai import APITimeoutError, OpenAI, OpenAIError, RateLimitError, omit

from server.detection_errors import (
    DetectionTimeoutError, DetectionUnavailableError, DetectionUpstreamError,
)

from server import settings
from server.analysis.prompt_policy import PromptSpec


def create_client():
    """按全局 AI_CONFIG 构造带超时的 OpenAI 客户端。"""
    # The SDK accepts a credential provider without an ambient key. For an
    # anonymous endpoint, chat_completion explicitly omits Authorization.
    return OpenAI(
        base_url=settings.AI_CONFIG["base_url"],
        api_key=settings.AI_CONFIG["api_key"] or (lambda: ""),
        timeout=settings.AI_CONFIG["timeout"],
    )


def chat_completion(client, *, messages):
    """唯一文本生成入口：模型及所有采样/预算参数均来自 AI_CONFIG。

    禁止业务调用方覆盖生成参数；改变策略应修改全局配置。
    """
    config = settings.AI_CONFIG
    kwargs = {
        "model": config["model"],
        "messages": messages,
        "temperature": config["temperature"],
        "max_tokens": config["max_tokens"],
    }
    if not config["api_key"]:
        kwargs["extra_headers"] = {"Authorization": omit}
    effort = config.get("reasoning_effort")
    if effort:
        kwargs["reasoning_effort"] = effort
    return client.chat.completions.create(**kwargs)


def complete_text(
    client, *, prompt: PromptSpec, input_data: object, empty_message: str,
) -> str:
    """Apply the canonical two-message prompt and return validated plain text.

    Callers provide a domain PromptSpec and JSON-serializable input facts;
    they cannot introduce alternate message layouts or embed facts in System.
    """
    completion = chat_completion(client, messages=prompt.messages(input_data))
    text = (completion.choices[0].message.content or "").strip() if completion.choices else ""
    if not text:
        raise DetectionUpstreamError(empty_message)
    return text


@contextmanager
def request_errors(operation: str):
    """Translate SDK failures; programming errors propagate to the HTTP boundary."""
    try:
        yield
    except (APITimeoutError, TimeoutError) as error:
        raise DetectionTimeoutError(f"{operation}服务响应超时") from error
    except RateLimitError as error:
        raise DetectionUnavailableError(f"{operation}服务繁忙，请稍后重试") from error
    except (OpenAIError, ConnectionError) as error:
        raise DetectionUpstreamError(f"{operation}上游服务调用失败") from error
