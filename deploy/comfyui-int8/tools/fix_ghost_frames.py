# -*- coding: utf-8 -*-
"""过渡"融化帧"手术修复：替换与前后帧都不像的孤立/成串坏帧。

**为什么需要**（2026-11-08 e2egreet01 25 帧过渡实测）：morph_fix 光流展开
和 fix_spike 混合会产出"融化帧"——人物半透明溶解（帧差 45~53，前后帧都
正常）。fix_spike 只处理单帧尖峰，成对出现的融化帧（t27+t28）漏网。

**判据**：坏帧 = 与前一帧、后一帧的灰度差**都** > th（正常运动帧至少贴着
一侧低差）。连续坏帧成 run [i..j]：整段用 帧i-1 → 帧j+1 的线性渐变替换
（快速 cross-dissolve，肉眼是干净的快过渡而非白闪）。首末帧永不触碰
（过渡端点必须与基础段一致）。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/fix_ghost_frames.py \
        --segments runs/make_avatar/<name>/segments [--th 12] [--apply]
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


def repair(frames, th=12.0):
    """返回 (新帧列表, 修复的 run 列表 [(i, j, 峰值)])。

    判据 = **跳跃对区间**：dp[k]（帧k→k+1 灰度差）> th 的两个"跳变"之间、
    中间帧差都 ≤ th 的整段 [i..j] 视为融化区——成对相似融化帧（中间帧差低）
    也能被两侧跳变夹住。整段用 帧(i-1) → 帧(j+1) 线性渐变替换（干净快过渡）。
    首末帧永不替换（端点必须与基础段一致，j+1 允许是末帧）。
    """
    n = len(frames)
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames]
    dp = [float(np.abs(gray[i] - gray[i - 1]).mean()) for i in range(1, n)]  # dp[k]=帧k→k+1
    runs, i = [], 1
    while i < n - 1:
        if dp[i - 1] > th:                      # 跳变进入帧 i
            j = i
            while j < n - 1 and dp[j] <= th:    # 中间帧差小 → 还在同一融化区
                j += 1
            if j < n - 1 and dp[j] > th and j >= i:   # 找到闭合跳变（j→j+1）
                peak = max(dp[i - 1], dp[j])
                runs.append((i, j, peak))
                # dp[j] 已被本区间当闭合跳变消费掉；帧 j+1 是正常侧第一帧，
                # 从 j+2 继续扫，否则同一条跳变会被当成 j+1 的进入跳变，
                # 把整个正常中段吞进区间（2026-11-08 实测教训）
                i = j + 2
                continue
        i += 1
    if not runs:
        return frames, []
    out = [f.copy() for f in frames]
    for i, j, _ in runs:
        a, b = frames[i - 1].astype(np.float32), frames[j + 1].astype(np.float32)
        m = j - i + 2
        for k, idx in enumerate(range(i, j + 1), start=1):
            alpha = k / m
            out[idx] = np.clip(a * (1 - alpha) + b * alpha, 0, 255).astype(np.uint8)
    return out, runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--only", default="", help="只处理这些段名（逗号分隔），空=全部过渡")
    ap.add_argument("--th", type=float, default=12.0)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    seg_dir = Path(args.segments)
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    vids = [p for p in sorted(seg_dir.glob("*.mp4"))
            if " to " in p.stem and (not only or p.stem in only)]
    if not vids:
        raise SystemExit("没有匹配的过渡段")
    fixed_total = 0
    for p in vids:
        frames, fps = read_frames(p)
        if len(frames) < 5:
            continue
        new, runs = repair(frames, args.th)
        if runs:
            peak = max(r[2] for r in runs)
            print(f"  {p.name}: 修复 {len(runs)} 处融化帧 {[(i, j) for i, j, _ in runs]}（峰值 {peak:.1f}）")
            fixed_total += len(runs)
            if args.apply:
                write_frames_atomic(p, new, fps)
        else:
            print(f"  {p.name}: 干净")
    print(f"共修复 {fixed_total} 处{'，已写回' if args.apply else '（dry，未写回）'}")


if __name__ == "__main__":
    main()
