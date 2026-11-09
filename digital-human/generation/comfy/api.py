# -*- coding: utf-8 -*-
"""ComfyUI REST 客户端 + 视频读帧/分析公共函数（v28 流程实测踩坑后的版本）。"""
import glob
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request

import cv2
import numpy as np

import config


def submit(prompt):
    """提交 API prompt，返回 prompt_id；校验失败打印服务端错误明细。"""
    data = json.dumps({"prompt": prompt}).encode()
    req = urllib.request.Request(config.SERVER + "/prompt", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30)).get("prompt_id")
    except urllib.error.HTTPError as e:
        print("校验失败:", json.dumps(json.load(e), ensure_ascii=False)[:1500], flush=True)
        return None


def wait(pid, tag="", timeout=1800):
    """轮询 /history 直到完成。ComfyUI 排队串行，一条过渡通常 3~6 分钟。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(15)
        try:
            h = json.load(urllib.request.urlopen(
                config.SERVER + "/history/" + pid, timeout=60))
        except Exception:
            continue
        e = h.get(pid)
        if e and e.get("status", {}).get("completed"):
            return e["status"].get("status_str") == "success"
    return False


def job_state(pid):
    """任务状态：missing（history 查不到）/ running / error / success。

    missing 也可能是 ComfyUI 重启后 history 被清空，调用方可用文件兜底。
    """
    try:
        h = json.load(urllib.request.urlopen(
            config.SERVER + "/history/" + pid, timeout=60))
    except Exception:
        return "missing", None
    e = h.get(pid)
    if not e:
        return "missing", None
    st = e.get("status") or {}
    if st.get("status_str") == "error":
        return "error", e
    if st.get("completed"):
        return "success", e
    return "running", e


def job_output_files(pid):
    """按 history 的 outputs 精确返回该任务产出的视频绝对路径。

    关键教训（2026-11-05）：SaveVideo 的同名前缀会递增版本号（_00001_、
    _00002_…）。旧产线硬编码 _00001_，把所有重掷任务都跳过——拿到的可能是
    上一次完全不同提示词/驱动产生的旧视频。必须用任务自己的 outputs。
    """
    state, e = job_state(pid)
    if state != "success" or not e:
        return []
    files = []
    for _node, info in (e.get("outputs") or {}).items():
        for key in ("images", "gifs", "videos"):
            for item in (info.get(key) or []):
                if not isinstance(item, dict):
                    continue
                name = item.get("filename") or ""
                if not name.lower().endswith((".mp4", ".webm", ".mov")):
                    continue
                p = os.path.join(config.COMFY_OUTPUT,
                                 item.get("subfolder") or "", name)
                if (os.path.isfile(p) and os.path.getsize(p) > 10000
                        and p not in files):
                    files.append(p)
    files.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return files


def latest_versioned(prefix):
    """兜底：history 不可用时取同前缀**最大版本号**（_00002_ 优先于 _00001_）。"""
    sub, base = os.path.split(prefix)
    d = os.path.join(config.COMFY_OUTPUT, sub)
    best = None
    for p in glob.glob(os.path.join(d, base + "_*.mp4")):
        m = re.search(r"_(\d{5})_?\.mp4$", os.path.basename(p))
        ver = int(m.group(1)) if m else -1
        key = (ver, os.path.getmtime(p))
        if best is None or key > best[0]:
            best = (key, p)
    return best[1] if best else None


def resolve_job_output(pid, prefix="", min_mtime=0.0):
    """解析提交任务的实际产出。

    优先级：history 精确 outputs > 同前缀最大版本号（仅 history missing 时）。
    任务运行中/报错一律返回 None——绝不回退到旧文件（旧产线最致命的坑）。
    兜底还要求文件 mtime 晚于提交时刻且 10 秒内没再变动，避免读到半成品。
    """
    files = job_output_files(pid)
    if files:
        return files[0]
    state, _ = job_state(pid)
    if state != "missing":
        return None
    p = latest_versioned(prefix) if prefix else None
    if not p:
        return None
    mtime = os.path.getmtime(p)
    if min_mtime and mtime < min_mtime:
        return None
    if time.time() - mtime < 10:
        return None
    return p


def frames_of(path, min_frames=30, retries=8, delay=4):
    """读全部帧。NVENC 写完文件句柄有延迟，刚生成完立刻读会读到 0 帧，必须重试。"""
    for _ in range(retries):
        cap = cv2.VideoCapture(path)
        fr = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            fr.append(f)
        cap.release()
        if len(fr) >= min_frames:
            return fr
        time.sleep(delay)
    return fr


def video_info(path):
    cap = cv2.VideoCapture(path)
    info = (int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS),
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return info


def upload_image(path):
    """上传文件到 ComfyUI input（图片/视频均可），返回服务端保存名。"""
    import requests
    with open(path, "rb") as f:
        r = requests.post(config.SERVER + "/api/upload/image",
                          files={"image": (os.path.basename(path), f)},
                          data={"overwrite": "1"}, timeout=60)
    return r.json()["name"]


def ensure_input_file(path):
    """把本地文件放进 ComfyUI input（已存在则跳过），返回 input 内的文件名。

    path 也可以直接是 input 里已有的文件名（不含目录分隔符），原样返回。
    """
    if os.sep not in path and "/" not in path and os.path.exists(
            os.path.join(config.COMFY_INPUT, path)):
        return path
    dst = os.path.join(config.COMFY_INPUT, os.path.basename(path))
    if not os.path.exists(dst):
        shutil.copy(path, dst)
    return os.path.basename(dst)


def imwrite_u(path, img):
    """中文路径安全的 PNG 写出（cv2.imwrite 对中文路径会静默失败）。"""
    ok, buf = cv2.imencode(".png", img)
    with open(path, "wb") as f:
        f.write(buf.tobytes())
    return ok


def gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)


def mean_of(frame):
    """整帧 BGR 均值（float64）。"""
    return frame.reshape(-1, 3).astype(np.float64).mean(0)


def encode_video_i420(frames, path, fps=20, bitrate="16M"):
    """帧列表编码为 h264 mp4。

    自己用 cv2 做 BT.601 I420 转换再喂 rawvideo，管道内不再做色彩转换；
    nvenc 与 VHS_VideoCombine 同编码器家族，可与 SCAIL 段 -c copy 无损拼接。
    （直接喂 bgr24 会让 swscale 按默认矩阵转换，暗场景每次暗 1~2 个色阶。）
    """
    import subprocess
    h, w = frames[0].shape[:2]
    yuv = [cv2.cvtColor(f, cv2.COLOR_BGR2YUV_I420) for f in frames]
    cmd = [config.ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}", "-r", str(fps),
           "-i", "-", "-c:v", "h264_nvenc", "-pix_fmt", "yuv420p",
           "-colorspace", "bt470bg", "-color_primaries", "bt470bg",
           "-b:v", bitrate, "-r", str(fps), path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for y in yuv:
        p.stdin.write(y.tobytes())
    p.stdin.close()
    p.wait()
    return path


def color_match_transition(idle_frames, trans_frames, greet_frames, work_dir, tag="t"):
    """LTX 过渡段与两侧 SCAIL 段的接缝色彩匹配（自校准，v31 实证：4 色阶 → ≤1.2）。

    原理：Q4 量化的 LTX 渲染域比 SCAIL 段暗约 4 个色阶，接缝出现可见台阶。
    逐帧施加 offset：首帧全量补偿「idle 末帧 - 过渡首帧」差，尾帧全量补偿
    「greet 首帧 - 过渡末帧」差，中间线性过渡并按 |2t-1| 平滑加权（中段低权重）。
    编码管道本身有约 -1 色阶的系统漂移，先编码一遍未校正帧实测漂移并烘进
    delta，避免校正量被管道吃掉（v1~v4 实测：不校准残差 2.5+，校准后 ≤1.2）。

    返回 (校正后帧列表, 编码后 mp4 路径)。
    """
    os.makedirs(work_dir, exist_ok=True)
    n = len(trans_frames)
    # 1. 实测编码管道在端点帧上的漂移
    calib_path = os.path.join(work_dir, f"{tag}_colormatch_calib.mp4")
    encode_video_i420(trans_frames, calib_path)
    calib = frames_of(calib_path, min_frames=n // 2)
    if len(calib) < n // 2:
        return trans_frames, None
    drift_first = mean_of(calib[0]) - mean_of(trans_frames[0])
    drift_last = mean_of(calib[-1]) - mean_of(trans_frames[-1])

    # 2. 目标 delta（烘进漂移）
    d_start = (mean_of(idle_frames[-1]) - mean_of(trans_frames[0])) - drift_first
    d_end = (mean_of(greet_frames[0]) - mean_of(trans_frames[-1])) - drift_last
    deltas = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.0
        w = abs(2 * t - 1)
        deltas.append(((1 - t) * d_start + t * d_end) * (0.35 + 0.65 * w))

    corrected = [np.clip(f.astype(np.float64) + d, 0, 255).astype(np.uint8)
                 for f, d in zip(trans_frames, deltas)]
    enc_path = os.path.join(work_dir, f"{tag}_colormatched.mp4")
    encode_video_i420(corrected, enc_path)
    return frames_of(enc_path, min_frames=n // 2), enc_path


def frame_diffs(frames):
    """相邻帧灰度差均值序列：动作剧烈程度。"""
    g = [gray(f) for f in frames]
    return [float(np.abs(g[i] - g[i + 1]).mean()) for i in range(len(g) - 1)]


def jitter_index(frames):
    """高频抖动指标：帧差序列的二阶差分绝对均值（越大越抖）。"""
    d = frame_diffs(frames)
    return float(np.mean([abs(d[i + 1] - d[i]) for i in range(len(d) - 1)]))


def roi_mean_diff(frames, y0, y1, x0, x1):
    """指定 ROI（如胸口区域）的相邻帧平均差，用来看呼吸/上身起伏。"""
    g = [gray(f)[y0:y1, x0:x1] for f in frames]
    return float(np.mean([np.abs(g[i] - g[i + 1]).mean() for i in range(len(g) - 1)]))
