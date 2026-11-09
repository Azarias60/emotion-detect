import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from .config import JWT_SECRET, TOKEN_TTL_SECONDS

_revoked_tokens = {}
_revoked_lock = threading.Lock()


def _b64encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return f"pbkdf2_sha256$310000${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, _b64decode(expected))
    except (ValueError, TypeError):
        return False


def create_token(user_id, username):
    now = int(time.time())
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload_data = {"sub": str(user_id), "username": username, "iat": now, "exp": now + TOKEN_TTL_SECONDS, "jti": secrets.token_hex(12)}
    payload = _b64encode(json.dumps(payload_data, separators=(",", ":")).encode())
    signature = _b64encode(hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def decode_token(token):
    try:
        header, payload, signature = token.split(".")
        expected = _b64encode(hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("令牌签名无效")
        claims = json.loads(_b64decode(payload))
        if int(claims["exp"]) <= int(time.time()):
            raise ValueError("登录已过期")
        with _revoked_lock:
            expired = [key for key, value in _revoked_tokens.items() if value <= int(time.time())]
            for key in expired:
                _revoked_tokens.pop(key, None)
            if claims.get("jti") in _revoked_tokens:
                raise ValueError("登录已注销")
        return claims
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(str(error) or "登录凭证无效")


def revoke_token(token):
    claims = decode_token(token)
    with _revoked_lock:
        _revoked_tokens[claims["jti"]] = int(claims["exp"])

