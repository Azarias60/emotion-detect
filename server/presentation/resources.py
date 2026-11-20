# -*-coding: utf-8 -*-
"""对象与模型配置 HTTP 适配：账号内集合的事实读取与意图提交。

集合读取返回账号全量行；变化经对象观察流到达使用方。删除意图先取得
实际引用者供确认弹窗呈现，删除只作用于目标对象及其自身资源。
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from server.envelope import Envelope, ErrorResponse, success

OBJECT_KINDS = ("configuration", "avatar", "voice", "material")

OBJECT_ERRORS = {
    400: {"model": ErrorResponse, "description": "请求参数不合法"},
    401: {"model": ErrorResponse, "description": "未登录或登录凭证无效"},
    404: {"model": ErrorResponse, "description": "对象不存在"},
}


def _kind_router(kind, collections):
    router = APIRouter(prefix=f"/objects/{kind}", tags=["对象"])

    @router.get("",
                response_model=Envelope[dict],
                responses={code: OBJECT_ERRORS[code] for code in (401,)})
    def list_objects(user=Depends(collections.principal)):
        return success({"items": collections.of(kind).list(user.user_id)})

    @router.post("",
                 response_model=Envelope[dict],
                 responses={code: OBJECT_ERRORS[code] for code in (400, 401)})
    def create_object(payload: dict, user=Depends(collections.principal)):
        return success(collections.of(kind).create(user.user_id, payload), "对象已创建")

    @router.get("/{object_id}",
                response_model=Envelope[dict],
                responses={code: OBJECT_ERRORS[code] for code in (401, 404)})
    def get_object(object_id: int, user=Depends(collections.principal)):
        try:
            return success(collections.of(kind).get(user.user_id, object_id))
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/{object_id}/update",
                 response_model=Envelope[dict],
                 responses={code: OBJECT_ERRORS[code] for code in (400, 401, 404)})
    def update_object(object_id: int, payload: dict, user=Depends(collections.principal)):
        try:
            collections.of(kind).update(user.user_id, object_id, payload)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return success(message="对象已更新")

    @router.get("/{object_id}/references",
                response_model=Envelope[dict],
                responses={code: OBJECT_ERRORS[code] for code in (401, 404)})
    def object_references(object_id: int, user=Depends(collections.principal)):
        try:
            references = collections.of(kind).references(user.user_id, object_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return success({"references": references})

    @router.post("/{object_id}/delete",
                 response_model=Envelope[None],
                 responses={code: OBJECT_ERRORS[code] for code in (400, 401, 404)})
    def delete_object(object_id: int, user=Depends(collections.principal)):
        try:
            collections.of(kind).remove(user.user_id, object_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return success(message="对象已删除")

    return router


class _Collections:
    """按 kind 分发的对象集合端口；装配时注入各类集合实例。"""

    def __init__(self, principal, by_kind):
        self.principal = principal
        self._by_kind = by_kind

    def of(self, kind):
        return self._by_kind[kind]


def create_objects_router(current_principal, by_kind: dict) -> APIRouter:
    collections = _Collections(current_principal, by_kind)
    router = APIRouter(tags=["对象"])
    for kind in OBJECT_KINDS:
        router.include_router(_kind_router(kind, collections))

    @router.get("/observations/objects/{kind}")
    async def object_stream(kind: str, user=Depends(current_principal)):
        import asyncio
        import json

        if kind not in OBJECT_KINDS:
            raise HTTPException(status_code=404, detail="对象类型不存在")
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def forward(rows):
            loop.call_soon_threadsafe(queue.put_nowait, rows)

        stop = by_kind[kind].observe(user.user_id, forward)

        async def stream():
            try:
                while True:
                    rows = await queue.get()
                    yield f"data: {json.dumps(rows, ensure_ascii=False)}\n\n"
            finally:
                stop()

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router


def create_model_config_router(current_principal, model_config) -> APIRouter:
    router = APIRouter(prefix="/model-config", tags=["模型配置"])

    @router.get("", response_model=Envelope[dict],
                responses={code: OBJECT_ERRORS[code] for code in (401,)})
    def read_config(user=Depends(current_principal)):
        key = model_config.read(user.user_id)
        masked = f"****{key[-4:]}" if len(key) > 4 else ("已配置" if key else "")
        return success({"configured": bool(key), "masked": masked})

    @router.post("", response_model=Envelope[None],
                 responses={code: OBJECT_ERRORS[code] for code in (400, 401)})
    def write_config(payload: dict, user=Depends(current_principal)):
        key = payload.get("apiKey")
        if not isinstance(key, str) or not key.strip():
            raise HTTPException(status_code=400, detail="请输入有效的模型 Key")
        model_config.set(user.user_id, key.strip())
        return success(message="模型 Key 已保存，下一轮对话生效")

    return router


__all__ = ["create_model_config_router", "create_objects_router"]
