"""Identity 的稳定公共类型；UserId 为账户域所有的用户标识。"""

from dataclasses import dataclass
from typing import NewType

UserId = NewType("UserId", int)


@dataclass(frozen=True)
class Principal:
    user_id: UserId
    username: str
    token_version: int = 0


@dataclass(frozen=True)
class AuthenticatedSession:
    """已验证的登录会话；有效期来自签名 claims，服务器侧不再续期。"""

    principal: Principal
    expires_at: float
