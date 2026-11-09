import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..config import UPLOAD_DIR
from ..dependencies import current_user
from ..responses import success

router = APIRouter(tags=["文件管理"])
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), user=Depends(current_user)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="仅支持 JPG、PNG、WEBP 图片")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过5MB")
    avatar_dir = UPLOAD_DIR / "avatars" / str(user["id"])
    avatar_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{suffix}"
    path = avatar_dir / filename
    with open(path, "wb") as output:
        output.write(content)
    relative = path.relative_to(UPLOAD_DIR).as_posix()
    return success({"url": f"/uploads/{relative}"}, "上传成功")

