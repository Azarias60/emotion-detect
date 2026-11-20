# -*-coding: utf-8 -*-
"""成功响应封套：{code, message, data}；code=0 表示成功。

错误不进封套，直面官方规范：HTTPException / 请求校验 / 域错误一律使用
官方形状（HTTPException 为 ``{"detail": ...}``，请求校验为 422 的结构化
``detail`` 数组），由框架默认处理器产出；业务域错误经集中映射为官方形状
的状态码与 detail。
"""
from collections.abc import Mapping
from typing import Generic, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict

DataT = TypeVar("DataT")

JSONValue: TypeAlias = (
    str | int | float | bool | None
    | list["JSONValue"]
    | Mapping[str, "JSONValue"]
)


class Envelope(BaseModel, Generic[DataT]):
    """成功响应封套：``data`` 绑定具体数据类型。"""

    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: DataT


class ErrorResponse(BaseModel):
    """官方形状的错误声明：``{"detail": "..."}``（HTTPException 语义）。"""

    model_config = ConfigDict(extra="forbid")

    detail: str


def success(data=None, message="成功"):
    """成功封套的唯一运行时构造器；形状由同文件 :class:`Envelope` 声明。"""
    return {"code": 0, "message": message, "data": data}
