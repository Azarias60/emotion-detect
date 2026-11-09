# -*- coding: utf-8 -*-
"""逐对择优：对 7 个补掷配对，从 {t11, b1(v3@57), b2(v4.2/s802)} 中选
尖峰最小且无黑场的版本复制为正式段文件。"""
import glob
import json
import shutil
import sys

import cv2
import numpy as np

OUT = r"E:\Comfyui\ComfyUI\No1基础包含文生图模型\ComfyUIportable\ComfyUIportable\ComfyUI\output"
SEG = r"E:\emotionDetect\digital-human\livetalking\guizi-character"
PAIRS = ["idle3 to idle1", "idle1 to greet", "idle1 to talk", "talk to idle1",
         "idle2 to idle3", "talk to greet", "greet to idle1"]


def load(p):
    cap = cv2.VideoCapture(p)
    fr = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        fr.append(f)
    cap.release()
    return fr


def metrics(fr):
    adj = [np.abs(fr[i].astype(int) - fr[i - 1].astype(int)).mean() for i in range(1, len(fr))]
    b = [f.mean() for f in fr]
    return max(adj), (min(b) < 15), b[0], b[-1]


def pick(cands):
    """cands: [(label, path)]，返回 (label, path, spike)。"""
    best = None
    for label, path in cands:
        if not path or not glob.glob(path):
            continue
        fr = load(glob.glob(path)[0])
        if len(fr) < 20:
            continue
        spike, dark, b0, b1 = metrics(fr)
        penalty = 40 if dark else 0
        score = spike + penalty
        if best is None or score < best[2]:
            best = (label, glob.glob(path)[0], score, spike, dark)
    return best


def t11_path(pair):
    return f"{OUT}\\gzt_guizi_t11_{pair.replace(' to ', '2')}_s801_00001.mp4"


def b_path(run, pair):
    return f"{OUT}\\gzt_guizi_{run}_{pair.replace(' to ', '2')}_s801_00001.mp4"


def b2_path(pair):
    return f"{OUT}\\gzt_guizi_b2_{pair.replace(' to ', '2')}_s802_00001.mp4"


report = []
for pair in PAIRS:
    cands = [
        ("t11", t11_path(pair)),
        ("b1", b_path("b1", pair)),
    ]
    bp = b2_path(pair)
    if bp:
        cands.append(("b2", bp))
    best = pick(cands)
    if best is None:
        report.append(f"{pair}: 无可用产出")
        continue
    label, src, score, spike, dark = best
    dst = f"{SEG}\\{pair}.mp4"
    shutil.copy2(src, dst)
    report.append(f"{pair}: 选 {label}（尖峰 {spike:.2f}{' ⚠黑场' if dark else ''}）-> 段文件")

print("\n".join(report))
