# -*-coding: utf-8 -*-
"""上传照片制作数字人：异步产线任务的提交、进度查询与管理。

流程：前端先 POST 照片拿到 job_id，再 POST 创建任务（后台 subprocess 跑
digital-human/studio/make_avatar.py 六阶段产线，数小时），期间 GET 轮询进度。
进度以磁盘上的 progress.json 为准——后端重启后任务状态仍可查。

2026-11-09 逻辑完善（本次修复的五个坑）：
  1. 并发闸原来只查进程内存表 _JOBS：后端一重启表就空了，而 make_avatar
     子进程还在跑 → 新任务穿过闸门，两个产线同时抢 ComfyUI/GPU，还会互相
     interrupt（11-06 那次全流程验证就是这样被第二个任务杀掉的）。
     现在闸门 = 内存 alive ∪（磁盘无 ok 的进度 ∧ 同名 make_avatar 进程活着）。
  2. 僵尸任务：原来"progress.json 没写 ok"就一律算 running，进程早死了也
     永远显示在跑，前端 resumeRunning 会接到死任务上假跑进度条。
     现在进程不在了标 interrupted，前端据此提示"任务已中断"。
  3. DELETE 任务原来只删目录不杀进程：子进程继续跑，还会把 progress.json
     重新写出来。现在先 terminate 再删。
  4. 重名检查原来只看 data/avatars/<name> 目录：presets.json 里有同 id 但
     目录不在时会出现两条同 id 预设，页面选一个两个一起变。现在一并查。
  5. 上传即预检（尺寸 + 正脸 + 人脸占比）：原来要等任务跑到阶段 0 才报
     "照片不合格"，用户已经提交了任务才知道。现在上传时就拒绝并说清原因。
另外：webp 直链真正转码成 jpg（原来扩展名与内容不符，靠 cv2 按内容解码
才没出错）；建新任务时清理 14 天前的历史任务目录。
"""
import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import cv2
import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["数字人制作"])

DH_ROOT = Path(__file__).resolve().parents[2] / "digital-human"
UPLOAD_DIR = DH_ROOT / "runs" / "uploads"
STUDIO_PY = DH_ROOT / "livetalking" / "venv" / "Scripts" / "python.exe"
MAKE_AVATAR = DH_ROOT / "studio" / "make_avatar.py"
ENGINE_DIR = DH_ROOT / "livetalking"
PRESETS_FILE = ENGINE_DIR / "data" / "presets.json"

# 进程内任务表：job_id -> {"proc": Popen | None, "name": str}
# 只存本进程创建的任务；历史任务靠 progress.json 只读查询。
_JOBS: dict[str, dict] = {}
# 名字规则与页面提示保持一致：字母开头，2~24 位字母/数字/下划线
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,23}$")
_NAME_HINT = "名字只能用 2~24 个英文字母、数字或下划线，且必须以字母开头"
# 上传预检：人脸宽度至少要占画面宽度的比例（与产线 stage_inspect 同标准）
_MIN_FACE_RATIO = 0.08
# 历史任务目录保留天数
_JOB_KEEP_DAYS = 14


def _progress_path(job_id: str) -> Path:
    return UPLOAD_DIR / job_id / "progress.json"


def _read_progress(job_id: str) -> dict | None:
    path = _progress_path(job_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _live_pipeline_names() -> set[str]:
    """正在运行的 make_avatar 任务名集合（按命令行里的 --name 提取）。

    一次 CIM 查询覆盖所有候选，避免每个任务各查一遍。
    查询失败（极端情况）返回空集合——此时宁可放行也不误拦。
    """
    # 优先 psutil（毫秒级、无子进程）；PowerShell CIM 每次 1-2s，前端 5 秒一轮
    # 轮询会把后端拖慢，且子进程偶发失败曾让 /jobs 整个 500（2026-11-19 实测）
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        names: set[str] = set()
        try:
            for proc in psutil.process_iter(["cmdline"]):
                cmd = " ".join(proc.info.get("cmdline") or [])
                if "make_avatar.py" not in cmd:
                    continue
                m = re.search(r"--name\s+([A-Za-z0-9_]+)", cmd)
                if m:
                    names.add(m.group(1))
        except Exception:
            return set()   # 宁可放行也不误拦
        return names

    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match 'make_avatar.py' } | "
        "ForEach-Object { $_.CommandLine }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=45).stdout or ""
    except Exception:
        return set()
    names = set()
    for line in out.splitlines():
        m = re.search(r"--name\s+([A-Za-z0-9_]+)", line)
        if m:
            names.add(m.group(1))
    return names


def _collect_note(data: dict) -> str | None:
    """读 SCAIL/过渡 collect 的分段进度 sidecar（runs/<ascii>_*/collect_progress.json）。

    生成动作段一跑 2~4 小时，主 progress.json 只在阶段边界更新——前端会看到
    冻结的百分比以为卡死（2026-11-20 实测）。sidecar 由 regen_scail/
    transition_queue 的 collect 循环每产出一段就写一份，这里合并进 message。
    """
    try:
        ascii_id = str(data.get("job_id") or "")
        if not ascii_id:
            return None
        best = None
        for mp in (DH_ROOT / "runs").glob(f"{ascii_id}_*/collect_progress.json"):
            try:
                d = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                continue
            done, total = int(d.get("done", 0)), int(d.get("total", 0))
            if total and (best is None or done > best[0]):
                best = (done, total, d.get("stage"))
        if not best or best[0] <= 0:
            return None
        label = "过渡" if best[2] == "transition" else "动作段"
        return f"{label} {best[0]}/{best[1]} 已产出"
    except Exception:
        return None


def _job_status(data: dict, live: set[str] | None = None) -> str:
    """由 progress.json 推导任务状态。

    running / completed / failed / interrupted（进程不在了但没写 ok）
    """
    if "ok" in data:
        return "completed" if data["ok"] else "failed"
    if live is None:
        live = _live_pipeline_names()
    # 进程命令行里的 --name 是 ASCII 任务名（progress.json 的 job_id）；
    # name 是中文显示名，拿它比对永远不匹配 → 运行中会被误判"已中断"
    ident = str(data.get("job_id") or data.get("name") or "")
    return "running" if ident in live else "interrupted"


def _preset_ids() -> set[str]:
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(p.get("id")) for p in data if isinstance(p, dict) and p.get("id")}
    except (OSError, json.JSONDecodeError):
        pass
    return set()


def _prune_old_jobs(live: set[str]) -> int:
    """清理超过保留期、且进程已不在的历史任务目录（只留照片与进度，删了不伤产线）。"""
    cutoff = datetime.now() - timedelta(days=_JOB_KEEP_DAYS)
    removed = 0
    if not UPLOAD_DIR.is_dir():
        return 0
    for path in UPLOAD_DIR.glob("*/progress.json"):
        data = _read_progress(path.parent.name) or {}
        if data.get("name") in live:
            continue
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                shutil.rmtree(path.parent, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("已清理 %d 个超过 %d 天的历史制作任务目录", removed, _JOB_KEEP_DAYS)
    return removed


def _save_photo(job_id: str, data: bytes, suffix: str) -> Path:
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / f"photo{suffix}"
    path.write_bytes(data)
    return path


def _precheck_photo(path: Path) -> str | None:
    """上传即预检：尺寸 + 正脸 + 人脸占比。返回 None=通过，否则是人话原因。

    人脸检测借用引擎 venv 的 insightface（与产线 stage_inspect 同一套，
    避免两处标准不一致）。检测环境不可用时放行——由产线阶段 0 兜底，
    不因为基础设施问题拦住用户上传。
    """
    img = cv2.imread(str(path))
    if img is None:
        return "照片无法读取（可能已损坏），请换一张"
    h, w = img.shape[:2]
    if w < 480 or h < 720:
        return f"照片分辨率太小（{w}×{h}），建议 720×1080 以上的半身正面照"
    if not STUDIO_PY.is_file():
        return None
    code = (
        "import sys, cv2, numpy as np\n"
        f"sys.path.insert(0, {str(ENGINE_DIR)!r})\n"
        f"sys.path.insert(0, {str(DH_ROOT)!r})\n"
        "from studio.qa_segments import load_detector\n"
        "det = load_detector()\n"
        "img = cv2.imread(sys.argv[1])\n"
        "r = det.get_detections_for_batch(np.array([img]))\n"
        "b = r[0] if r and r[0] is not None else None\n"
        "if b is None:\n"
        "    print('NOFACE')\n"
        "else:\n"
        "    print('FACE %.4f' % ((b[2] - b[0]) / img.shape[1]))\n"
    )
    try:
        out = subprocess.run([str(STUDIO_PY), "-c", code, str(path)],
                             capture_output=True, text=True, timeout=120,
                             cwd=str(DH_ROOT)).stdout.strip()
    except Exception:
        return None  # 环境问题不拦，交给产线阶段 0
    if out == "NOFACE":
        return "照片里没有检测到正脸——请换一张光线充足、正脸清晰的半身照"
    m = re.match(r"FACE ([0-9.]+)", out)
    if m and float(m.group(1)) < _MIN_FACE_RATIO:
        return f"照片里的人脸太小（占宽 {float(m.group(1)) * 100:.1f}%）——请离镜头近一些，拍半身照"
    return None


@router.post("/dh/avatar/photo")
async def upload_photo(file: UploadFile = File(...)):
    """接收用户照片，存到任务目录，返回 job_id。上传即做尺寸/正脸预检。"""
    if (file.content_type or "") not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "只支持 jpg / png / webp 图片")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 10MB")
    job_id = uuid.uuid4().hex[:12]
    if (file.content_type or "").endswith("webp"):
        # webp 真正转码成 jpg：扩展名与内容必须一致（产线与 cv2 都按后缀/内容两头看）
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(400, "webp 图片解码失败，请换一张")
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise HTTPException(400, "webp 图片转码失败，请换一张")
        data, suffix = buf.tobytes(), ".jpg"
    else:
        suffix = ".png" if (file.content_type or "").endswith("png") else ".jpg"
    path = _save_photo(job_id, data, suffix)
    reason = _precheck_photo(path)
    if reason:
        shutil.rmtree(path.parent, ignore_errors=True)
        raise HTTPException(400, reason)
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
    限制：仅 http/https、图片 content-type、10MB、15 秒超时；webp 转码为 jpg。
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
    raw = resp.content
    if len(raw) < 1024:
        raise HTTPException(400, "图片内容为空或过小")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 10MB")
    if ctype in ("image/webp",):
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(400, "webp 图片解码失败，请换一个链接")
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise HTTPException(400, "webp 图片转码失败，请换一个链接")
        data, suffix = buf.tobytes(), ".jpg"
    elif ctype in ("image/jpeg", "image/jpg", "image/png"):
        data = raw
        suffix = ".png" if ctype == "image/png" else ".jpg"
    else:
        raise HTTPException(400, f"链接不是图片（content-type={ctype or '未知'}）")
    job_id = uuid.uuid4().hex[:12]
    path = _save_photo(job_id, data, suffix)
    reason = _precheck_photo(path)
    if reason:
        shutil.rmtree(path.parent, ignore_errors=True)
        raise HTTPException(400, reason)
    logger.info("照片直链已抓取 job_id=%s (%s, %d bytes)", job_id, url[:120], len(data))
    return {"job_id": job_id}


@router.post("/dh/engine/stop")
async def engine_stop():
    """停数字人引擎（8020）——制作产线让出显存用。

    引擎常驻 7~9G 显存（wav2lip+CosyVoice3），与 SCAIL/LTX 争 16G 会互相
    拖死（实测引擎在线时 ComfyUI OOM、TTS 75s 超时）。只停本后端拉起的
    引擎进程，后端本身不重启，检测/首页全程不受影响。
    """
    from core.dh_engine import stop_engine

    stopped = stop_engine()
    return {"stopped": stopped, "message": "数字人引擎已停止" if stopped else "引擎未在运行"}


@router.post("/dh/engine/start")
async def engine_start():
    """拉数字人引擎（8020）——制作结束后恢复。

    已在监听则不重复拉起；加载模型约需 30~60 秒，期间对话暂不可用。
    """
    from core.dh_engine import start_engine

    started = start_engine()
    return {"started": started,
            "message": "数字人引擎恢复中（约 1 分钟）" if started else "引擎已在运行"}


@router.post("/dh/avatar/jobs")
async def create_job(body: JobCreate):
    """创建制作任务：一个照片任务对应一个后台 make_avatar 进程。"""
    if not _NAME_RE.match(body.name or ""):
        raise HTTPException(400, _NAME_HINT)
    job_dir = UPLOAD_DIR / body.job_id
    photos = list(job_dir.glob("photo.*")) if job_dir.is_dir() else []
    if not photos:
        raise HTTPException(404, "任务目录不存在或照片未上传，请先上传照片")
    if _progress_path(body.job_id).is_file():
        raise HTTPException(409, "该照片已创建过任务，请重新上传照片")

    live = _live_pipeline_names()
    _prune_old_jobs(live)
    # 并发闸：本进程内存表 ∪ 磁盘上"没写完且进程还活着"的任务
    # （后端重启后 _JOBS 是空的，但子进程还在跑——只查内存会漏放）
    busy_mem = [jid for jid, j in _JOBS.items() if j["proc"] and j["proc"].poll() is None]
    busy_disk = []
    if UPLOAD_DIR.is_dir():
        for path in UPLOAD_DIR.glob("*/progress.json"):
            data = _read_progress(path.parent.name) or {}
            if "ok" not in data and data.get("name") in live:
                busy_disk.append(data.get("name"))
    if busy_mem or busy_disk:
        who = busy_disk[0] if busy_disk else _JOBS[busy_mem[0]]["name"]
        raise HTTPException(409, f"已有制作任务「{who}」在进行中（GPU 串行），请等它完成再提交")

    # 重名检查：素材目录 ∪ presets.json 注册表（两边都可能撞）
    if (ENGINE_DIR / "data" / "avatars" / body.name).is_dir():
        raise HTTPException(409, f"数字人 id「{body.name}」已存在，请换一个名字")
    if body.name in _preset_ids():
        raise HTTPException(409, f"数字人 id「{body.name}」已存在于配对表，请换一个名字")

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
    data = _read_progress(job_id)
    if data is None:
        meta = _JOBS.get(job_id)
        proc_alive = bool(meta and meta["proc"] and meta["proc"].poll() is None)
        if proc_alive:
            return {"job_id": job_id, "status": "running", "stage": "准备中",
                    "stage_index": 0, "message": "任务已提交"}
        raise HTTPException(404, "任务不存在")
    status = _job_status(data)
    if status == "running" and data.get("stage") in ("生成动作段", "生成过渡"):
        note = _collect_note(data)
        if note:
            data["message"] = f"{data.get('message', '')}（{note}）".strip("（）")
    return {**data, "status": status}


@router.get("/dh/avatar/jobs")
async def job_list():
    """全部任务（扫磁盘，含历史）。status 与单任务接口同源推导，
    前端 resumeRunning 靠它刷新后重新接上运行中的任务。"""
    live = _live_pipeline_names()
    result = []
    if UPLOAD_DIR.is_dir():
        for path in sorted(UPLOAD_DIR.glob("*/progress.json")):
            data = _read_progress(path.parent.name)
            if data is None:
                continue
            # 先算状态再摘 job_id：_job_status 要用它对齐进程命令行里的 --name
            status = _job_status(data, live)
            if status == "running" and data.get("stage") in ("生成动作段", "生成过渡"):
                note = _collect_note(data)
                if note:
                    data["message"] = f"{data.get('message', '')}（{note}）".strip("（）")
            data.pop("job_id", None)
            data["status"] = status
            result.append({"job_id": path.parent.name, **data})
    return {"jobs": result}


def _purge_comfyui_tasks(ascii_name: str) -> int:
    """把某数字人任务残留在 ComfyUI 队列里的生成项清掉（取消后不再占 GPU）。

    2026-11-20 实测：取消只杀了 make_avatar，ComfyUI pending 里排着的段
    继续占 GPU 几小时。产出前缀固定为 scail2/<ascii>_，按它匹配。
    """
    import urllib.request

    try:
        from generation import config as comfy_config
        server = comfy_config.SERVER
    except Exception:
        return 0
    tag = f"scail2/{ascii_name}_"
    try:
        q = json.load(urllib.request.urlopen(server + "/queue", timeout=30))
    except Exception:
        return 0
    pids: list[str] = []
    interrupt_running = False
    for state in ("queue_running", "queue_pending"):
        for item in q.get(state, []):
            try:
                text = json.dumps(item[2], ensure_ascii=False)
            except Exception:
                continue
            if tag in text:
                if len(item) > 1:
                    pids.append(str(item[1]))
                if state == "queue_running":
                    interrupt_running = True
    if not pids and not interrupt_running:
        return 0
    try:
        if interrupt_running:
            urllib.request.urlopen(urllib.request.Request(
                server + "/interrupt", data=b"", method="POST"), timeout=30)
        if pids:
            req = urllib.request.Request(
                server + "/queue",
                data=json.dumps({"delete": pids}).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30)
        logger.info("ComfyUI 队列已清理 ascii=%s 剩余项=%s 打断运行中=%s",
                    ascii_name, len(pids), interrupt_running)
        return len(pids)
    except Exception as error:
        logger.warning("ComfyUI 队列清理失败（不影响删除本身）：%s", error)
        return 0


@router.delete("/dh/avatar/jobs/{job_id}")
async def job_delete(job_id: str):
    """取消并删除任务：先终止产线进程，再删目录。

    原来只删目录不杀进程——子进程会把 progress.json 重新写出来，目录删了
    又长回来，任务还在后台跑。现在先 terminate，等它退出，再删。
    """
    job_dir = UPLOAD_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(404, "任务不存在")
    meta = _JOBS.pop(job_id, None)
    proc = meta.get("proc") if meta else None
    killed_here = False
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        killed_here = True
        logger.info("制作任务已取消 job_id=%s", job_id)
    # 后端重启过的话 _JOBS 已空（内存表），按命令行里任务目录路径兜底搜进程：
    # make_avatar 的 --photo/--progress-json 都含 uploads/<job_id>，重启后
    # 取消按钮此前只会删目录不杀进程——任务继续占 GPU 跑到天荒地老
    # （2026-11-20 实测：DELETE 返回 cancelled:false，9 个残留进程）。
    ascii_name = ""
    try:
        pj = job_dir / "progress.json"
        if pj.is_file():
            ascii_name = str(json.loads(pj.read_text(encoding="utf-8")).get("job_id") or "")
    except Exception:
        ascii_name = ""
    try:
        import psutil
        marker = f"uploads{chr(92)}{job_id}{chr(92)}"
        marker_alt = f"uploads/{job_id}/"
        victims = []
        for ps_proc in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(ps_proc.info.get("cmdline") or [])
            if "make_avatar.py" not in cmd:
                continue
            if marker in cmd or marker_alt in cmd:
                victims.append(ps_proc)
        for victim in victims:
            try:
                for child in victim.children(recursive=True):
                    child.kill()
                victim.kill()
            except Exception:
                pass
        if victims:
            killed_here = True
            logger.info("制作任务已取消（按命令行兜底） job_id=%s pids=%s",
                        job_id, [v.info["pid"] if hasattr(v, "info") else 0 for v in victims])
    except Exception:
        logger.exception("取消任务的进程兜底搜索失败（目录仍会删除）")
    if ascii_name:
        _purge_comfyui_tasks(ascii_name)
    shutil.rmtree(job_dir, ignore_errors=True)
    return {"deleted": job_id, "cancelled": bool(killed_here)}
