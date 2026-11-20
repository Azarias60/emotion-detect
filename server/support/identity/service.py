"""Identity 用例与登录失败限流。"""

import threading
import time

from types import ModuleType
from typing import Callable
from server.support.identity.types.ports import UserRepository
from server.support.identity.types import AuthenticatedSession, Principal
from server.support.identity.types.errors import (
    InvalidCredentialsError, InvalidPasswordError, LoginRateLimitedError,
    UserNotFoundError, UsernameTakenError,
)


class LoginLimiter:
    WINDOW_SECONDS = 60
    MAX_FAILURES = 5
    MAX_TRACKED = 4096

    def __init__(self, now=time.time):
        self._now, self._failures, self._lock = now, {}, threading.Lock()

    def _prune(self, now):
        for name in [name for name, attempts in self._failures.items() if not attempts or now - attempts[-1] > self.WINDOW_SECONDS]:
            del self._failures[name]
        while len(self._failures) > self.MAX_TRACKED:
            self._failures.pop(next(iter(self._failures)))

    def blocked(self, username):
        now = self._now()
        with self._lock:
            self._prune(now)
            return sum(now - item < self.WINDOW_SECONDS for item in self._failures.get(username, ())) >= self.MAX_FAILURES

    def record_failure(self, username):
        now = self._now()
        with self._lock:
            recent = [item for item in self._failures.get(username, []) if now - item < self.WINDOW_SECONDS]
            self._failures[username] = recent + [now]
            self._prune(now)

    def clear(self, username):
        with self._lock:
            self._failures.pop(username, None)


def serialize_user(row):
    return {
        "id": row["id"], "username": row["username"], "gender": row["gender"] or "",
        "age": row["age"], "email": row["email"] or "", "userPic": row["user_pic"] or "",
        "createTime": row["created_at"], "updateTime": row["updated_at"],
    }


class IdentityService:
    def __init__(self, users: UserRepository, security: ModuleType, clock: Callable[[], str]):
        self.users = users
        self.security = security
        self.clock = clock
        self.limiter = LoginLimiter()

    def register(self, username, password):
        if self.users.get_by_username(username):
            raise UsernameTakenError("用户名已存在")
        user_id = self.users.create(username, self.security.hash_password(password), self.clock())
        return {"id": user_id}

    def login(self, username, password):
        if self.limiter.blocked(username):
            raise LoginRateLimitedError("登录失败次数过多，请稍后重试")
        row = self.users.get_by_username(username)
        if row is None or not self.security.verify_password(password, row["password_hash"]):
            self.limiter.record_failure(username)
            raise InvalidCredentialsError("用户名或密码错误")
        self.limiter.clear(username)
        return {"token": self.security.create_token(row["id"], row["username"], row["token_version"] or 0), "user": serialize_user(row)}

    def authenticate_token(self, token) -> AuthenticatedSession:
        """验证登录令牌并返回会话；有效期取自签名 claims 的 exp。"""
        try:
            claims = self.security.decode_token(token)
            expires_at = float(claims["exp"])
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidCredentialsError(str(error) or "登录凭证无效") from error
        try:
            user_id = int(claims["sub"])
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidCredentialsError("登录凭证无效") from error
        row = self._get_user(user_id)
        if int(claims.get("ver", 0)) != int(row["token_version"] or 0):
            raise InvalidCredentialsError("登录已注销，请重新登录")
        principal = Principal(user_id=user_id, username=row["username"],
                              token_version=row["token_version"] or 0)
        return AuthenticatedSession(principal=principal, expires_at=expires_at)

    def principal_from_token(self, token):
        return self.authenticate_token(token).principal

    def _get_user(self, user_id):
        row = self.users.get_by_id(user_id)
        if row is None:
            raise UserNotFoundError("用户不存在")
        return row

    def profile(self, user_id):
        return serialize_user(self._get_user(user_id))

    def update_profile(self, user_id, values):
        row = self._get_user(user_id)
        if values["username"] != row["username"] and self.users.get_by_username(values["username"]):
            raise UsernameTakenError("用户名已存在")
        return serialize_user(self.users.update_profile(user_id, values, self.clock()))

    def update_password(self, user_id, current_password, new_password):
        row = self._get_user(user_id)
        if not self.security.verify_password(current_password, row["password_hash"]):
            raise InvalidPasswordError("原密码错误")
        self.users.update_password(user_id, self.security.hash_password(new_password), self.clock())

    def logout(self, user_id):
        self.users.increment_token_version(user_id)
