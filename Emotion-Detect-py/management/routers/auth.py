import threading
import time

from fastapi import APIRouter, Depends, Header, HTTPException

from ..database import database, utc_now
from ..dependencies import current_token, current_user
from ..responses import success
from ..schemas import LoginRequest, RegisterRequest
from ..security import create_token, hash_password, revoke_token, verify_password

router = APIRouter(prefix="/user", tags=["账号管理"])
_attempts = {}
_attempt_lock = threading.Lock()


def _user_payload(row):
    return {
        "id": row["id"], "username": row["username"], "gender": row["gender"] or "",
        "age": row["age"], "email": row["email"] or "", "userPic": row["user_pic"] or "",
        "createTime": row["created_at"], "updateTime": row["updated_at"],
    }


@router.post("/register")
def register(request: RegisterRequest):
    if request.password != request.rePassword:
        raise HTTPException(status_code=400, detail="两次密码需一致")
    now = utc_now()
    with database() as connection:
        if connection.execute("SELECT id FROM users WHERE username=?", (request.username,)).fetchone():
            raise HTTPException(status_code=409, detail="用户名已存在")
        cursor = connection.execute(
            "INSERT INTO users(username,password_hash,created_at,updated_at) VALUES(?,?,?,?)",
            (request.username, hash_password(request.password), now, now),
        )
        user_id = cursor.lastrowid
    return success({"id": user_id}, "注册成功")


@router.post("/login")
def login(request: LoginRequest):
    now = time.time()
    with _attempt_lock:
        recent = [item for item in _attempts.get(request.username, []) if now - item < 60]
        if len(recent) >= 5:
            raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后重试")
    with database() as connection:
        row = connection.execute("SELECT * FROM users WHERE username=?", (request.username,)).fetchone()
    if row is None or not verify_password(request.password, row["password_hash"]):
        with _attempt_lock:
            _attempts[request.username] = recent + [now]
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    with _attempt_lock:
        _attempts.pop(request.username, None)
    token = create_token(row["id"], row["username"])
    return success({"token": token, "user": _user_payload(row)}, "登录成功")


@router.post("/logout")
def logout(token: str = Depends(current_token), user=Depends(current_user)):
    revoke_token(token)
    return success(message="退出成功")

