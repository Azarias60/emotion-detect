# -*-coding: utf-8 -*-
"""过渡段污染普查：每条 "A to B.mp4" 的首帧应≈A 段尾帧、尾帧应≈B 段首帧。
像素级均值差超过阈值=被其他素材污染（如鬼子库串扰）。纯本地比对，不碰 GPU。"""
import sys
from pathlib import Path

import cv2
import numpy as np

seg_dir = Path(sys.argv[1])
TH = 40.0  # 均值绝对差阈值（0-255）：编码噪声 <8，换人 >60


def frames_of(p, idxs):
    cap = cv2.VideoCapture(str(p))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(max(0, n - 1) if i == -1 else i, max(0, n - 1)))
        ok, f = cap.read()
        out.append(f if ok else None)
    cap.release()
    return [f for f in out if f is not None]


def gray_mean_diff(a, b):
    if a is None or b is None:
        return 999.0
    ga = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (128, 128))
    gb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (128, 128))
    return float(np.mean(np.abs(ga.astype(np.float32) - gb.astype(np.float32))))


def base_tail(name):
    cap = cv2.VideoCapture(str(seg_dir / f"{name}.mp4"))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n - 1))
    ok, f = cap.read()
    cap.release()
    return f if ok else None


def base_head(name):
    cap = cv2.VideoCapture(str(seg_dir / f"{name}.mp4"))
    ok, f = cap.read()
    cap.release()
    return f if ok else None


bad = []
for video in sorted(seg_dir.glob("* to *.mp4")):
    a, b = video.stem.split(" to ")
    fr = frames_of(video, [0, -1])
    first, last = (fr[0] if fr else None), (fr[-1] if len(fr) > 1 else None)
    d_head = gray_mean_diff(first, base_tail(a))
    d_tail = gray_mean_diff(last, base_head(b))
    ok = d_head < TH and d_tail < TH
    print(f"{video.stem:<18} 首帧vs{a}尾帧={d_head:6.1f}  尾帧vs{b}首帧={d_tail:6.1f}  {'✓' if ok else '✗ 污染'}")
    if not ok:
        bad.append(video.stem)
print("\n污染过渡:", bad if bad else "无")
sys.exit(1 if bad else 0)
