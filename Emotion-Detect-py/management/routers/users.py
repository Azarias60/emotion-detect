import re

from fastapi import APIRouter, Depends, HTTPException

from ..database import database, utc_now
from ..dependencies import current_user
from ..responses import success
from ..schemas import PasswordUpdateRequest, UserUpdateRequest
from ..security import hash_password, verify_password

router = APIRouter(prefix="/user", tags=["用户管理"])


def serialize_user(row):
    return {
        "id": row["id"], "username": row["username"], "gender": row["gender"] or "",
        "age": row["age"], "email": row["email"] or "", "userPic": row["user_pic"] or "",
        "createTime": row["created_at"], "updateTime": row["updated_at"],
    }


@router.get("/userInfo")
def user_info(user=Depends(current_user)):
    return success(serialize_user(user))


@router.put("/update")
@router.post("/update")
def update_user(request: UserUpdateRequest, user=Depends(current_user)):
    if request.email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", request.email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    now = utc_now()
    with database() as connection:
        duplicate = connection.execute("SELECT id FROM users WHERE username=? AND id<>?", (request.username, user["id"])).fetchone()
        if duplicate:
            raise HTTPException(status_code=409, detail="用户名已存在")
        connection.execute(
            "UPDATE users SET username=?,gender=?,age=?,email=?,user_pic=?,updated_at=? WHERE id=?",
            (request.username, request.gender, request.age, request.email, request.userPic, now, user["id"]),
        )
        updated = connection.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    return success(serialize_user(updated), "资料更新成功")


@router.put("/updatePwd")
@router.post("/updatePwd")
def update_password(request: PasswordUpdateRequest, user=Depends(current_user)):
    if request.newPassword != request.newRePassword:
        raise HTTPException(status_code=400, detail="两次输入的新密码不一致")
    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码错误")
    with database() as connection:
        connection.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (hash_password(request.newPassword), utc_now(), user["id"]))
    return success(message="密码修改成功")

