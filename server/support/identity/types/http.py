"""身份域 HTTP 响应 DTO：与 serialize_user / service 返回逐字段一致。"""
from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterResult(StrictModel):
    id: int


class UserProfile(StrictModel):
    id: int
    username: str
    gender: str
    age: int | None
    email: str
    userPic: str
    createTime: str
    updateTime: str


class LoginResult(StrictModel):
    token: str
    user: UserProfile


class AvatarUploaded(StrictModel):
    url: str


__all__ = ["AvatarUploaded", "LoginResult", "RegisterResult", "UserProfile"]
