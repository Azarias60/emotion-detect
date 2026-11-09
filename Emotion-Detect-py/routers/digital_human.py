# -*-coding: utf-8 -*-
"""上传照片制作数字人：异步产线任务的提交与进度查询。

流程：前端先 POST 照片拿到 job_id，再 POST 创建任务（后台 subprocess 跑
digital-human/studio/make_avatar.py 八阶段产线，1~2.5 小时），期间 GET 轮询
进度。进度以磁盘上的 progress.json 为准——后端重启后任务状态仍可查，
但进程句柄丢失后无法取消（任务会继续在后台跑完）。

并发闸：ComfyUI 串行队列 + GPU 独占，同时只允许 1 个 running 任务。
"""
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["数字人制作"])

DH_ROOT = Path(__file__).resolve().parents[2] / "digital-human"
UPLOAD_DIR = DH_ROOT / "runs" / "uploads"
STUDIO_PY = DH_ROOT / "livetalking" / "venv" / "Scripts" / "python.exe"
MAKE_AVATAR = DH_ROOT / "studio" / "make_avatar.py"

# 进程内任务表：job_id -> {"proc": Popen | None, "name": str}
# 只存本进程创建的任务；历史任务靠 progress.json 只读查询。
_JOBS: dict[str, dict] = {}
_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{1,23}$")


def _progress_path(job_id: str) -> Path:
    return DH_ROOT / "runs" / "uploads" / job_id / "progress.json"


@router.post("/dh/avatar/photo")
async def upload_photo(file: UploadFile = File(...)):
    """接收用户照片，存到任务目录，返回 job_id。"""
    if (file.content_type or "") not in ("image/jpeg", "image/png"):
        raise HTTPException(400, "只支持 jpg / png 图片")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 10MB")
    job_id = uuid.uuid4().hex[:12]
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".png" if (file.content_type or "").endswith("png") else ".jpg"
    (job_dir / f"photo{suffix}").write_bytes(data)
    logger.info("照片已上传 job_id=%s (%d bytes)", job_id, len(data))
    return {"job_id": job_id}


class JobCreate(BaseModel):
    job_id: str
    name: str
    display_name: str = ""


class PhotoUrl(BaseModel):
    url: str


@router.post("/dh/avatar/photo-url")
async def photo_from_url(body: PhotoUrl):
    """直链下载照片：抓取网上图片 URL → 落地到任务目录，返回 job_id。

    与上传接口同一产物形态（<job_id>/photo.<ext>），产线无感知。
    限制：仅 http/https、图片 content-type、10MB、15 秒超时。
    """
    url = (body.url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(400, "只支持 http/https 直链")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:
        raise HTTPException(502, f"图片下载失败：{exc}")
    if resp.status_code != 200:
        raise HTTPException(502, f"图片下载失败（HTTP {resp.status_code}）")
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    suffix = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
              "image/webp": ".jpg"}.get(ctype)
    if not suffix:
        raise HTTPException(400, f"链接不是图片（content-type={ctype or '未知'}）")
    data = resp.content
    if len(data) < 1024:
        raise HTTPException(400, "图片内容为空或过小")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 10MB")
    job_id = uuid.uuid4().hex[:12]
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"photo{suffix}").write_bytes(data)
    logger.info("照片直链已抓取 job_id=%s (%s, %d bytes)", job_id, url[:120], len(data))
    return {"job_id": job_id}


@router.post("/dh/avatar/jobs")
async def create_job(body: JobCreate):
    """创建制作任务：一个照片任务对应一个后台 make_avatar 进程。"""
    if not _NAME_RE.match(body.name or ""):
        raise HTTPException(400, "名字只能用 2~24 个英文字母/数字（用于内部标识）")
    job_dir = UPLOAD_DIR / body.job_id
    photos = list(job_dir.glob("photo.*")) if job_dir.is_dir() else []
    if not photos:
        raise HTTPException(404, "任务目录不存在或照片未上传，请先上传照片")
    if _progress_path(body.job_id).is_file():
        raise HTTPException(409, "该照片已创建过任务，请重新上传照片")
    running = [jid for jid, j in _JOBS.items() if j["proc"] and j["proc"].poll() is None]
    if running:
        raise HTTPException(409, "已有一个制作任务在进行中（GPU 串行），请等它完成再提交")

    if (DH_ROOT / "livetalking" / "data" / "avatars" / body.name).is_dir():
        raise HTTPException(409, f"数字人 id「{body.name}」已存在，请换一个名字")

    progress = _progress_path(body.job_id)
    cmd = [str(STUDIO_PY), str(MAKE_AVATAR),
           "--photo", str(photos[0]), "--name", body.name,
           "--display-name", body.display_name or body.name,
           "--progress-json", str(progress)]
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    proc = subprocess.Popen(cmd, cwd=str(DH_ROOT), creationflags=flags,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _JOBS[body.job_id] = {"proc": proc, "name": body.name}
    logger.info("制作任务已创建 job_id=%s name=%s pid=%s", body.job_id, body.name, proc.pid)
    return {"job_id": body.job_id, "pid": proc.pid}


@router.get("/dh/avatar/jobs/{job_id}")
async def job_status(job_id: str):
    """读进度：progress.json 为准（后端重启后仍可查历史任务）。"""
    path = _progress_path(job_id)
    if not path.is_file():
        meta = _JOBS.get(job_id)
        proc_alive = bool(meta and meta["proc"] and meta["proc"].poll() is None)
        if proc_alive:
            return {"job_id": job_id, "status": "running", "stage": "准备中",
                    "stage_index": 0, "message": "任务已提交"}
        raise HTTPException(404, "任务不存在")
    data = json.loads(path.read_text(encoding="utf-8"))
    status = "running"
    if "ok" in data:
        status = "completed" if data["ok"] else "failed"
    return {**data, "status": status}


@router.get("/dh/avatar/jobs")
async def job_list():
    """全部任务（扫磁盘，含历史）。status 与单任务接口同源推导，
    前端 resumeRunning 靠它刷新后重新接上运行中的任务。"""
    result = []
    if UPLOAD_DIR.is_dir():
        for path in sorted(UPLOAD_DIR.glob("*/progress.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            data.pop("job_id", None)
            if "ok" in data:
                data["status"] = "completed" if data["ok"] else "failed"
            else:
                data["status"] = "running"
            result.append({"job_id": path.parent.name, **data})
    return {"jobs": result}


@router.delete("/dh/avatar/jobs/{job_id}")
async def job_delete(job_id: str):
    """删除任务目录（上传的照片与进度）。"""
    job_dir = UPLOAD_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(404, "任务不存在")
    shutil.rmtree(job_dir, ignore_errors=True)
    _JOBS.pop(job_id, None)
    return {"deleted": job_id}
