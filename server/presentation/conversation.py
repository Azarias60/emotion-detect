# -*-coding: utf-8 -*-
"""导览与陪伴会话 HTTP 适配：页面表达使用条件与交流意图。

上下文的使用期（页面、登录）由页面表达，由后端按自身存在条件裁决；
文字发送直接交互，语音输入由后端语音链路进入同一上下文。回复在请求
响应中返回；引擎未接入时以 503 明确呈现，不做替身对话。
"""
from fastapi import APIRouter, Depends, HTTPException
from server.envelope import Envelope, ErrorResponse, success

CONVERSATION_ERRORS = {
    400: {"model": ErrorResponse, "description": "请求参数不合法"},
    401: {"model": ErrorResponse, "description": "未登录或登录凭证无效"},
    404: {"model": ErrorResponse, "description": "会话不存在"},
    503: {"model": ErrorResponse, "description": "数字人引擎未接入"},
}

CONVERSATION_KINDS = ("guide", "companion")


def create_conversation_router(current_principal, contexts) -> APIRouter:
    """contexts: kind → 每账号上下文端口（set_usage/send/hear/restart/set_login）。"""
    router = APIRouter(tags=["会话"])

    def _context(kind, user):
        context = contexts.get(kind, user.user_id)
        if context is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return context

    @router.post("/conversation/{kind}/usage",
                 response_model=Envelope[None],
                 responses={code: CONVERSATION_ERRORS[code] for code in (400, 401, 404, 503)})
    async def express_usage(kind: str, payload: dict, user=Depends(current_principal)):
        if payload.get("active") is True:
            await _context(kind, user).set_usage(True)
        else:
            await contexts.get(kind, user.user_id).set_usage(False)
        return success(message="已更新会话使用条件")

    @router.post("/conversation/{kind}/send",
                 response_model=Envelope[dict],
                 responses={code: CONVERSATION_ERRORS[code] for code in (400, 401, 404, 503)})
    async def send_text(kind: str, payload: dict, user=Depends(current_principal)):
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=400, detail="请输入对话内容")
        reply = await _context(kind, user).send(text.strip())
        return success({"reply": reply}, "已回复")

    @router.post("/conversation/{kind}/restart",
                 response_model=Envelope[None],
                 responses={code: CONVERSATION_ERRORS[code] for code in (400, 401, 404, 503)})
    async def restart(kind: str, user=Depends(current_principal)):
        await _context(kind, user).restart()
        return success(message="已重新开始")

    return router


class AccountContexts:
    """按账号与类别装配会话上下文；引擎由装配注入（缺失时明确不可用）。"""

    def __init__(self, model_config, engine_factory=None):
        self._model_config = model_config
        self._engine_factory = engine_factory
        self._by_key: dict[tuple[str, int], object] = {}

    def get(self, kind, owner_id):
        from server.conversation.service import create_context
        key = (kind, owner_id)
        context = self._by_key.get(key)
        if context is None:
            if self._engine_factory is None:
                return None
            context = create_context(
                owner_id=owner_id, login_id=str(owner_id), kind=kind,
                engine=self._engine_factory(kind),
                model_config=self._model_config,
            )
            self._by_key[key] = context
        return context


__all__ = ["AccountContexts", "create_conversation_router"]
