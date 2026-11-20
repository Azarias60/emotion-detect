"""供 HTTP 消费方使用的认证边界，只返回 Principal。

装配根用 ``IdentityDependencies`` 把 IdentityService 绑定为请求依赖；
处理器经闭包引用服务，不做 app.state 或全局注册表查找。
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.support.identity.service import IdentityService
from server.support.identity.types.errors import IdentityError

# 官方安全依赖负责 ``Authorization: Bearer`` 的提取与 OpenAPI 安全声明；
# auto_error=False 让缺失/形状错误收敛为本域 401（RFC 6750：带
# ``WWW-Authenticate``），不落框架默认的 403。令牌必须带 Bearer 方案。
_bearer = HTTPBearer(auto_error=False, scheme_name="UserToken",
                     description="Identity 登录令牌")


class IdentityDependencies:
    """绑定一个身份服务的请求依赖集合；``identity`` 可在装配后被整体替换。"""

    def __init__(self, identity: IdentityService):
        self.identity = identity

    def get_identity(self):
        return self.identity

    def current_principal(
        self,
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ):
        if credentials is None:
            raise HTTPException(status_code=401, detail="请先登录",
                                headers={"WWW-Authenticate": "Bearer"})
        try:
            return self.identity.principal_from_token(credentials.credentials)
        except IdentityError as error:
            raise HTTPException(status_code=401, detail=str(error),
                                headers={"WWW-Authenticate": "Bearer"}) from None


__all__ = ["IdentityDependencies"]
