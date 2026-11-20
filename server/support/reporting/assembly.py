"""Reporting composition; imported only by the assembly root."""

from server.support.reporting.adapters.sqlite import SqliteReportRepository
from server.support.reporting.service import ReportingService


def create_reporting_service(database_path) -> ReportingService:
    """Wire the sqlite archive store behind the idempotent reporting service."""
    return ReportingService(SqliteReportRepository(database_path))
