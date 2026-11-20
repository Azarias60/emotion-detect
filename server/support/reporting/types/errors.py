"""Reporting 公共错误类型。"""


class ReportNotFoundError(RuntimeError):
    """报告不存在或不属于当前账号。"""
