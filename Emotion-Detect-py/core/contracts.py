"""HTTP 输入契约；不依赖设备或模型实现。"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CaptureSource = Literal["hardware"]


class CaptureRequest(BaseModel):
    record_duration: int = Field(default=60, ge=1, le=600)
    capture_video: bool = True


class TransportRequest(BaseModel):
    mode: str = ""
    host: str | None = None
    tcp_port: int | None = Field(default=None, ge=1, le=65535)
    udp_port: int | None = Field(default=None, ge=1, le=65535)
    port: str | None = None
    baudrate: int | None = Field(default=None, gt=0)

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, value):
        return str(value).strip().lower()


class ConsultationInput(BaseModel):
    tongue: dict = Field(default_factory=dict)
    questions: list[str] = Field(default_factory=list)
    answers: list[str | dict] = Field(default_factory=list)
