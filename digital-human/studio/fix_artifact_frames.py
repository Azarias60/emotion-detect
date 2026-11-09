# -*- coding: utf-8 -*-
"""视觉定位的定点坏帧修复：把指定帧区间整帧替换为两端帧的线性渐变。

**为什么需要**（2026-11-08 e2egreet01 视觉终验实测）：SCAIL+后处理链会产出
**空间域孤立坏帧**——帧差查不出（运动统计正常），只有视觉能发现：
  - encourage t24-25：抬臂中前臂溶解消失、腰侧悬浮残影
  - greet t57：手掌半透明重影
  - idle1 t42：右拱门阴影竖条消失（背景带帧差 9.16 vs 其他段 ≤2.12）
这类帧的统一修法 = 邻帧跨溶解（人物动作慢，80ms 内位置差小，
溶解结果就是自然运动模糊）。区间由 GLM 视觉核验定位后人工指定。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/fix_artifact_frames.py \
        --segments runs/make_avatar/<name>/segments \
        --spec "encourage=24-25;greet=57;idle1=42" --apply
"""
import argparse
import os
from pathlib import Path

import cv2
import numpy as np


def read_frames(path):
    cap = cv2.VideoCapture(str(path))
    fr = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        fr.append(f)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()
    return fr, fps


def write_frames_atomic(path, frames, fps):
    tmp = path.with_suffix(".tmp.mp4")
    h, w = frames[0].shape[:2]
    out = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in frames:
        out.write(f)
    out.release()
    os.replace(tmp, path)


def parse_spec(spec):
    """"encourage=24-25;greet=57;idle1=42" → {段名: [(起, 止), ...]}"""
    out = {}
    for part in spec.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, rng = part.split("=", 1)
        spans = []
        for r in rng.split(","):
            r = r.strip()
            if "-" in r:
                a, b = r.split("-")
                spans.append((int(a), int(b)))
            else:
                spans.append((int(r), int(r)))
        out[name.strip()] = spans
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--spec", required=True, help='"段=帧区间(可多段);..."')
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    seg_dir = Path(args.segments)
    plan = parse_spec(args.spec)
    for name, spans in plan.items():
        p = seg_dir / f"{name}.mp4"
        if not p.is_file():
            print(f"  {name}: 文件不存在，跳过")
            continue
        frames, fps = read_frames(p)
        n = len(frames)
        changed = 0
        for a, b in spans:
            a, b = max(a, 1), min(b, n - 2)   # 首末帧永不触碰
            if a > b:
                continue
            m = b - a + 2
            fa = frames[a - 1].astype(np.float32)
            fb = frames[b + 1].astype(np.float32)
            for k, idx in enumerate(range(a, b + 1), start=1):
                alpha = k / m
                frames[idx] = np.clip(fa * (1 - alpha) + fb * alpha,
                                      0, 255).astype(np.uint8)
            changed += b - a + 1
            print(f"  {name}: 帧 {a}-{b} 已用 {a - 1}→{b + 1} 跨溶解替换")
        if changed and args.apply:
            write_frames_atomic(p, frames, fps)
    print(f"完成{'（已写回）' if args.apply else '（dry，未写回）'}")


if __name__ == "__main__":
    main()
