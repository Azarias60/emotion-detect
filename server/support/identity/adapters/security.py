"""密码与 JWT 适配器。"""

import secrets
import time

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from server import settings

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return _password_hash.verify(password, encoded)
    except (UnknownHashError, ValueError, TypeError):
        return False


def create_token(user_id: int, username: str, token_version: int) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id), "username": username, "iat": now,
        "exp": now + settings.TOKEN_TTL_SECONDS, "jti": secrets.token_hex(12),
        "ver": int(token_version),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as error:
        raise ValueError("登录已过期") from error
    except jwt.InvalidTokenError as error:
        raise ValueError("登录凭证无效") from error
