# -*- coding: utf-8 -*-
"""过渡段尾段沉降裁剪（只剪末尾真静止，不动中间任何帧）。

背景（2026-11-05 全帧视觉核验）：LTX FLF2V 在两端姿势接近时会提前到达目标
姿势，随后进入 0.6~1.0 秒的低运动"收尾沉降"；用户明确要求不要停顿。
实测：在长静止段起点截断后，与下一段首帧的差仅 3.2~5.0（原端点差 3.0~3.9
同量级），说明收尾沉降是多余的。

规则（保守，宁可少剪）：
  1. 只处理含 " to " 的过渡段；
  2. 逐帧计算人物区 MAD（避开背景），找**最后一段**连续 >=10 帧、MAD<0.4
     的低运动段；
  3. 截断到该段起点前一帧；要求至少保留 30 帧、且截断帧与下一段首帧的
     整帧平均差 <=6.0（否则不剪，避免制造跳变）；
  4. 已裁过的文件（<=60 帧）直接跳过，保证幂等。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/trim_transition_tail.py \
        --segments runs/make_avatar/<name>/segments [--apply]
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "generation")):
    if p not in sys.path:
        sys.path.insert(0, p)

import config

TH = 0.4          # 人物区 MAD 低运动阈值（0-255 灰度）
MIN_RUN = 10      # 至少连续这么多帧才算收尾沉降
MIN_KEEP = 30     # 裁剪后至少保留帧数
MAX_END_DIFF = 6.0
ALREADY_TRIMMED = 60  # 81 帧生成一次裁到 51~57；<=60 视为已裁，幂等


def frames_of(p):
    c = cv2.VideoCapture(str(p))
    fr = []
    while True:
        ok, f = c.read()
        if not ok:
            break
        fr.append(f)
    fps = c.get(cv2.CAP_PROP_FPS) or 25.0
    c.release()
    return fr, fps


def person_mad(frames):
    """人物区（画面中部）相邻帧 MAD 序列。"""
    h, w = frames[0].shape[:2]
    y0, y1 = int(h * 0.12), int(h * 0.94)
    x0, x1 = int(w * 0.20), int(w * 0.80)
    out = []
    prev = None
    for f in frames:
        g = cv2.cvtColor(f[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            out.append(float(np.abs(g - prev).mean()))
        prev = g
    return out


def first_frame(p):
    c = cv2.VideoCapture(str(p))
    ok, f = c.read()
    c.release()
    return f


def trim_one(p: Path, next_seg: Path, apply: bool):
    frames, fps = frames_of(p)
    n = len(frames)
    if n <= ALREADY_TRIMMED:
        return None
    ds = person_mad(frames)  # ds[i] = diff(frame i, frame i+1)
    runs, start = [], None
    for i, d in enumerate(ds):
        if d < TH:
            if start is None:
                start = i
        else:
            if start is not None:
                runs.append((start, i - 1, i - start))
                start = None
    if start is not None:
        runs.append((start, len(ds) - 1, len(ds) - start))
    long_runs = [r for r in runs if r[2] >= MIN_RUN]
    if not long_runs:
        return None
    run = max(long_runs, key=lambda r: r[0])
    trim_to = run[0] - 1  # keep 0..trim_to
    if trim_to < MIN_KEEP:
        return None
    nb = first_frame(next_seg)
    diff = float(np.abs(frames[trim_to].astype(int) - nb.astype(int)).mean())
    print(f"{p.name:22s} {n}帧 尾段静止 {run[2]}帧[{run[0]}-{run[1]}] "
          f"-> 裁到 {trim_to + 1}帧  端点差 {diff:.1f}")
    if diff > MAX_END_DIFF:
        print(f"{p.name:22s} 端点差 {diff:.1f} > {MAX_END_DIFF}，不剪")
        return None
    if apply:
        bak = p.parent / "_pre_tailtrim"
        bak.mkdir(exist_ok=True)
        if not (bak / p.name).is_file():
            shutil.copy2(p, bak / p.name)
        tmp = p.with_suffix(".tailtrim.mp4")
        cmd = [config.ffmpeg_exe(), "-y", "-i", str(p),
               "-frames:v", str(trim_to + 1), "-c:v", "libx264",
               "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
               "-r", str(round(fps)), str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"{p.name:22s} ffmpeg 失败: {r.stderr[-200:]}")
            return None
        tmp.replace(p)
    return {"seg": p.stem, "from": n, "to": trim_to + 1, "end_diff": round(diff, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    d = Path(args.segments)
    changed = []
    for p in sorted(d.glob("* to *.mp4")):
        b = p.stem.split(" to ")[1]
        nxt = d / f"{b}.mp4"
        if not nxt.is_file():
            continue
        r = trim_one(p, nxt, args.apply)
        if r:
            changed.append(r)
    print(f"处理 {len(changed)} 条" + ("" if args.apply else "（dry-run）"))


if __name__ == "__main__":
    main()
