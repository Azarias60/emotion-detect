from fastapi import Header, HTTPException

from .database import database
from .security import decode_token


def extract_token(authorization):
    if not authorization:
        raise HTTPException(status_code=401, detail="请先登录")
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else authorization.strip()


def current_user(authorization: str = Header(default="")):
    token = extract_token(authorization)
    try:
        claims = decode_token(token)
    except ValueError as error:
        raise HTTPException(status_code=401, detail=str(error))
    with database() as connection:
        row = connection.execute("SELECT * FROM users WHERE id=?", (int(claims["sub"]),)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return dict(row)


def current_token(authorization: str = Header(default="")):
    return extract_token(authorization)

