# -*-coding: utf-8 -*-
"""账号 HTTP 适配：注册/登录/登出。

登录限流为进程内有界窗口（单进程部署边界见 docs/architecture.md §3）；
登出走用户级 token_version 自增——所有已签发令牌一并失效，语义为
"退出全部会话"，且重启后依然有效。
"""
from fastapi import APIRouter, Depends, HTTPException
from server.support.identity.types.requests import RegisterRequest, LoginRequest

from server.envelope import Envelope, ErrorResponse, success
from server.support.identity.http_dependencies import IdentityDependencies
from server.support.identity.types.errors import InvalidCredentialsError, LoginRateLimitedError, UsernameTakenError
from server.support.identity.types.http import LoginResult, RegisterResult

def create_auth_router(deps: IdentityDependencies) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["账号"])

    @router.post("/register", response_model=Envelope[RegisterResult],
                 responses={400: {"model": ErrorResponse, "description": "两次密码需一致"},
                            409: {"model": ErrorResponse, "description": "用户名已存在"}})
    def register(request: RegisterRequest, identity_service=Depends(deps.get_identity)):
        if request.password != request.rePassword:
            raise HTTPException(status_code=400, detail="两次密码需一致")
        try:
            return success(identity_service.register(request.username, request.password), "注册成功")
        except UsernameTakenError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post("/login", response_model=Envelope[LoginResult],
                 responses={401: {"model": ErrorResponse, "description": "用户名或密码错误"},
                            429: {"model": ErrorResponse, "description": "登录失败次数过多"}})
    def login(request: LoginRequest, identity_service=Depends(deps.get_identity)):
        try:
            return success(identity_service.login(request.username, request.password), "登录成功")
        except LoginRateLimitedError as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        except InvalidCredentialsError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

    @router.post("/logout", response_model=Envelope[None],
                 responses={401: {"model": ErrorResponse, "description": "未登录或登录凭证无效"}})
    def logout(user=Depends(deps.current_principal), identity_service=Depends(deps.get_identity)):
        """注销 = 用户令牌版本自增；该用户全部已签发令牌立即失效。"""
        identity_service.logout(user.user_id)
        return success(message="退出成功")

    return router
