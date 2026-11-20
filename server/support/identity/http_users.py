# -*-coding: utf-8 -*-
"""用户 HTTP 适配：资料、密码和头像。"""
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from server.support.identity.types.requests import UserUpdateRequest, PasswordUpdateRequest

from server.envelope import Envelope, ErrorResponse, success
from server.support.identity.http_dependencies import IdentityDependencies
from server.support.identity.types.errors import InvalidPasswordError, UsernameTakenError
from server.support.identity.types.http import AvatarUploaded, UserProfile

USER_ERRORS = {
    400: {"model": ErrorResponse, "description": "请求参数不合法"},
    401: {"model": ErrorResponse, "description": "未登录或登录凭证无效"},
    409: {"model": ErrorResponse, "description": "用户名已存在"},
}
AVATAR_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_AVATAR_BYTES = 5 * 1024 * 1024


def create_users_router(deps: IdentityDependencies, upload_root: Path) -> APIRouter:
    router = APIRouter(prefix="/users", tags=["用户"])

    @router.get("/me", response_model=Envelope[UserProfile],
                responses={401: USER_ERRORS[401]})
    def user_info(user=Depends(deps.current_principal), identity_service=Depends(deps.get_identity)):
        return success(identity_service.profile(user.user_id))

    @router.patch("/me", response_model=Envelope[UserProfile],
                  responses=USER_ERRORS)
    def update_user(request: UserUpdateRequest, user=Depends(deps.current_principal), identity_service=Depends(deps.get_identity)):
        if request.email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", request.email):
            raise HTTPException(status_code=400, detail="邮箱格式不正确")
        try:
            updated = identity_service.update_profile(user.user_id, {"username": request.username, "gender": request.gender,
                "age": request.age, "email": request.email, "user_pic": request.userPic})
        except UsernameTakenError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return success(updated, "资料更新成功")

    @router.patch("/me/password", response_model=Envelope[None],
                  responses={code: USER_ERRORS[code] for code in (400, 401)})
    def update_password(request: PasswordUpdateRequest, user=Depends(deps.current_principal), identity_service=Depends(deps.get_identity)):
        if request.newPassword != request.newRePassword:
            raise HTTPException(status_code=400, detail="两次输入的新密码不一致")
        try:
            identity_service.update_password(user.user_id, request.password, request.newPassword)
        except InvalidPasswordError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return success(message="密码修改成功")

    @router.post("/me/avatar", response_model=Envelope[AvatarUploaded], responses={
        400: USER_ERRORS[400],
        401: USER_ERRORS[401],
        413: {"model": ErrorResponse, "description": "上传文件超过大小限制"},
    })
    def upload_avatar(file: UploadFile = File(...), user=Depends(deps.current_principal)):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in AVATAR_SUFFIXES:
            raise HTTPException(status_code=400, detail="仅支持 JPG、PNG、WEBP 图片")
        content = file.file.read(MAX_AVATAR_BYTES + 1)
        if not content:
            raise HTTPException(status_code=400, detail="上传文件为空")
        if len(content) > MAX_AVATAR_BYTES:
            raise HTTPException(status_code=413, detail="文件不能超过5MB")
        relative = Path("avatars") / str(user.user_id) / f"{uuid4().hex}{suffix}"
        path = upload_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return success({"url": f"/uploads/{relative.as_posix()}"}, "上传成功")

    return router
