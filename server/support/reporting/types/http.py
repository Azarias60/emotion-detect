"""报告域 HTTP 响应 DTO：与 ReportingService 的载荷逐字段一致。

报告是同一轮检测业务数据的唯一事实来源：``result`` 保存本轮各过程
提供的业务事实（问答、指标、解读等），标题与摘要由显示端从这些事实
派生，不作为报告的业务字段。
"""
from pydantic import BaseModel, ConfigDict, JsonValue


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportItem(StrictModel):
    id: int
    type: str
    createUserId: int
    createTime: str
    result: dict[str, JsonValue]


class ReportCollection(StrictModel):
    items: list[ReportItem]


class ReportStarted(StrictModel):
    id: int


__all__ = [
    "ReportCollection",
    "ReportItem",
    "ReportStarted",
]
