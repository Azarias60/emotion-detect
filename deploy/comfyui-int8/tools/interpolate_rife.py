# -*- coding: utf-8 -*-
"""把 SCAIL 20fps 动作段用 RIFE 插帧到 25fps（消除 build_avatar 的补帧抖动）。

为什么需要：SCAIL 输出 20fps，引擎固定 25fps 播放。build_avatar 用 ffmpeg
`fps=25` 是**复制帧**不是插帧——每 5 帧里有 1 帧重复，动作出现规律性微顿
（2026-11-05 实测：jerk 0.226）。RIFE 2x（20→40）后重采样到 25fps，
jerk 降到 0.104，且首末帧保持不变（过渡端点不受影响）。

只处理基础动作段（不含 " to "），过渡段由 LTX 直接以 25fps 生成。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/interpolate_rife.py \
        --segments runs/make_avatar/<name>/segments_v5 --apply
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "generation")):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
from comfy import api

CKPT = "rife49.pth"


def probe(path):
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()
    return n, fps


def frames_of(path):
    cap = cv2.VideoCapture(str(path))
    fr = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        fr.append(f)
    cap.release()
    return fr


def jerk(frames):
    g = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames]
    d = np.array([np.abs(g[i] - g[i - 1]).mean() for i in range(1, len(g))])
    return float(np.mean(np.abs(np.diff(d)))) if len(d) > 2 else 0.0, d


def build_graph(input_name, prefix, src_fps, multiplier):
    return {
        "1": {"class_type": "VHS_LoadVideo", "inputs": {
            "video": input_name, "force_rate": 0, "custom_width": 0,
            "custom_height": 0, "frame_load_cap": 0, "skip_first_frames": 0,
            "select_every_nth": 1, "format": "Wan"}},
        "2": {"class_type": "RIFE VFI", "inputs": {
            "ckpt_name": CKPT, "frames": ["1", 0],
            "clear_cache_after_n_frames": 10, "multiplier": multiplier,
            "fast_mode": True, "ensemble": True, "scale_factor": 1.0,
            "dtype": "float32", "torch_compile": False, "batch_size": 1}},
        "3": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["2", 0], "frame_rate": int(round(src_fps * multiplier)),
            "loop_count": 0, "filename_prefix": prefix,
            "format": "video/h264-mp4", "pingpong": False, "save_output": True,
            "pix_fmt": "yuv420p", "bitrate": 10, "megabit": True,
            "save_metadata": True}},
    }


def interpolate_one(src: Path, target_fps: int, multiplier: int, apply: bool):
    n, fps = probe(src)
    if fps >= target_fps - 0.1:
        print(f"{src.name:20s} 已是 {fps:.1f}fps，跳过")
        return True
    tag = hashlib.sha1(f"{src.resolve()}|{src.stat().st_mtime_ns}".encode()).hexdigest()[:8]
    up_name = f"rife_{src.stem}_{tag}.mp4"
    up_path = Path(config.COMFY_INPUT) / up_name
    if not up_path.is_file() or up_path.stat().st_size != src.stat().st_size:
        shutil.copy2(src, up_path)
    prefix = f"rife/{src.stem}_{tag}"
    pid = api.submit(build_graph(up_name, prefix, fps, multiplier))
    if not pid:
        print(f"{src.name:20s} 提交失败")
        return False
    print(f"{src.name:20s} {n}帧@{fps:.1f} -> RIFE {multiplier}x  pid={pid}", flush=True)
    t0 = time.time()
    out = None
    while time.time() - t0 < 3600:
        time.sleep(10)
        state, _ = api.job_state(pid)
        if state == "success":
            out = api.resolve_job_output(pid, prefix)
            if out:
                break
        elif state == "error":
            print(f"{src.name:20s} RIFE 任务报错")
            return False
    if not out:
        print(f"{src.name:20s} 等待超时")
        return False

    # 40fps -> 25fps（只丢帧，不复制）。临时文件必须放同盘：Windows 的
    # Path.replace 跨盘会报 WinError 17（C:\Temp -> E: 实测）。
    tmp = src.parent / f".{src.stem}_25fps.tmp.mp4"
    cmd = [config.ffmpeg_exe(), "-y", "-i", out, "-vf", f"fps={target_fps}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "16",
           "-pix_fmt", "yuv420p", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"{src.name:20s} ffmpeg 失败: {r.stderr[-300:]}")
        return False

    before = frames_of(src)
    after = frames_of(tmp)
    if len(after) < 5:
        print(f"{src.name:20s} 插帧产出无帧")
        return False
    d0 = float(np.abs(before[0].astype(int) - after[0].astype(int)).mean())
    d1 = float(np.abs(before[-1].astype(int) - after[-1].astype(int)).mean())
    j0, _ = jerk(before)
    j1, diff = jerk(after)
    dup = int((diff < 0.02).sum())
    print(f"{src.name:20s} {n}->{len(after)}帧  jerk {j0:.3f}->{j1:.3f}"
          f"  重复帧 {dup}  端点差 {d0:.1f}/{d1:.1f}", flush=True)
    if max(d0, d1) > 3.0:
        print(f"{src.name:20s} ⚠端点被插帧改变，放弃写回")
        return False
    if apply:
        backup = src.parent / "_pre_rife"
        backup.mkdir(exist_ok=True)
        if not (backup / src.name).is_file():
            shutil.copy2(src, backup / src.name)
        tmp.replace(src)
    else:
        tmp.unlink(missing_ok=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--target-fps", type=int, default=25)
    ap.add_argument("--multiplier", type=int, default=2)
    ap.add_argument("--only-action", action="store_true", default=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    ok = True
    for p in sorted(Path(args.segments).glob("*.mp4")):
        if args.only_action and " to " in p.stem:
            continue
        ok = interpolate_one(p, args.target_fps, args.multiplier, args.apply) and ok
    print("完成" if ok else "有失败项")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
