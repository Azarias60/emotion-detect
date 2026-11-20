from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=16)
    password: str = Field(min_length=5, max_length=64)
    rePassword: str = Field(min_length=5, max_length=64)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=16)
    password: str = Field(min_length=5, max_length=64)


class UserUpdateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=16)
    gender: str = Field(default="", max_length=16)
    age: int | None = Field(default=None, ge=1, le=120)
    email: str = Field(default="", max_length=128)
    userPic: str = Field(default="", max_length=500)


class PasswordUpdateRequest(BaseModel):
    password: str
    newPassword: str = Field(min_length=5, max_length=64)
    newRePassword: str = Field(min_length=5, max_length=64)
