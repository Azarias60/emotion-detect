# -*-coding: utf-8 -*-
"""Reporting HTTP 适配：账号范围的报告集合、详情、删除与启动建立。

路由由 ``create_reporting_router`` 工厂构建：认证依赖与 ReportingService
在装配时注入，模块本身不持有模块级服务实例。匹配筛选由消费端从观察
到的集合派生，服务端不再提供分页查询。
"""
from fastapi import APIRouter, Depends, HTTPException

from server.envelope import Envelope, ErrorResponse, success
from server.support.reporting.service import ReportingService
from server.support.reporting.types.errors import ReportNotFoundError
from server.support.reporting.types.http import (
    ReportCollection, ReportItem, ReportStarted,
)

REPORT_ERRORS = {
    400: {"model": ErrorResponse, "description": "请求参数不合法"},
    401: {"model": ErrorResponse, "description": "未登录或登录凭证无效"},
    404: {"model": ErrorResponse, "description": "报告不存在"},
}


def create_reporting_router(current_principal, service: ReportingService) -> APIRouter:
    """构建报告路由；``current_principal`` 为装配时注入的认证依赖。"""
    router = APIRouter(prefix="/reports", tags=["报告"])

    @router.get("", response_model=Envelope[ReportCollection],
                responses={code: REPORT_ERRORS[code] for code in (401,)})
    def report_list(user=Depends(current_principal)):
        return success(service.list_reports(user.user_id))

    @router.get("/{report_id}", response_model=Envelope[ReportItem],
                responses={code: REPORT_ERRORS[code] for code in (401, 404)})
    def report_detail(report_id: int, user=Depends(current_principal)):
        try:
            data = service.report_detail(user.user_id, report_id)
        except ReportNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return success(data)

    @router.delete("/{report_id}", response_model=Envelope[None],
                   responses={code: REPORT_ERRORS[code] for code in (401, 404)})
    def delete_report(report_id: int, user=Depends(current_principal)):
        try:
            service.delete_report(user.user_id, report_id)
        except ReportNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return success(message="报告删除成功")

    return router
