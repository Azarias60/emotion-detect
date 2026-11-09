# -*- coding: utf-8 -*-
"""嘴部静默化（studio QA 修复工序）：把生成段里"该闭嘴却轻微动"的嘴部压平。

**为什么需要**（2026-11-08 e2egreet01 greet 实测）：SCAIL+RIFE 链路后 greet
嘴/额比 p90=3.36（门 2.8），而两个原始种子只有 1.64/1.67——后处理把嘴部
闪动放大了。引擎运行时 wav2lip 会在嘴区重画口型，生成段的嘴部必须完全
静止，否则双重嘴。

**方法**：腿部稳定化（stabilize_legs.py）同款运动门控时间低通，但**去掉
slow 项**——腿部要保留真实慢运动，嘴部任何运动都不要（hf 门控即可：
w = clip((hf - t0)/t1, 0, 1)，动的像素向 13 帧时间均值拉拢 → 张开的帧被
拉回"统计上占多数的闭嘴态"）。ROI 取全体帧脸盒中位数决定的嘴区矩形
（QA 实测漂移仅 1.5px，固定矩形安全），边缘高斯羽化防接缝。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/stabilize_mouth.py \
        --segments runs/make_avatar/<name>/segments --only greet --apply
"""
import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "livetalking"
for p in (str(ENGINE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from studio.qa_segments import load_detector, sample_frames  # noqa: E402
from studio.stabilize_legs import KER, RADIUS, hf_of  # noqa: E402


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


def mouth_roi(frames, det, y0=0.52, y1=1.06, x0=0.20, x1=0.80):
    """全体帧脸盒中位数 → 嘴区矩形（含下巴余量），越界夹紧。"""
    boxes = []
    for f in frames:
        rects = det.get_detections_for_batch(np.array([f]))
        if rects and rects[0] is not None:
            boxes.append(rects[0])
    if len(boxes) < len(frames) * 0.5:
        return None
    bx = np.median([b[0] for b in boxes])
    by = np.median([b[1] for b in boxes])
    bw = np.median([b[2] - b[0] for b in boxes])
    bh = np.median([b[3] - b[1] for b in boxes])
    h, w = frames[0].shape[:2]
    rx1 = int(np.clip(bx + x0 * bw, 0, w - 1))
    rx2 = int(np.clip(bx + x1 * bw, rx1 + 4, w))
    ry1 = int(np.clip(by + y0 * bh, 0, h - 1))
    ry2 = int(np.clip(by + y1 * bh, ry1 + 4, h))
    return rx1, ry1, rx2, ry2


def process(path, strength=0.9, t0=0.5, t1=1.5):
    frames, fps = read_frames(path)
    n = len(frames)
    if n < 2 * RADIUS + 2:
        return None
    det = load_detector()
    roi = mouth_roi(frames, det)
    if roi is None:
        return None
    rx1, ry1, rx2, ry2 = roi
    h, w = frames[0].shape[:2]

    # ROI 内灰度栈 → hf（QA 同款时间高频）
    gray = np.stack([cv2.cvtColor(f[ry1:ry2, rx1:rx2], cv2.COLOR_BGR2GRAY)
                     for f in frames]).astype(np.float32)
    from studio.stabilize_legs import _KS
    ma = gray.copy()
    for _ in range(2):
        pad = np.pad(ma, ((2, 2), (0, 0), (0, 0)), mode="edge")
        ma = sum(pad[i:i + len(ma)] * _KS[i] for i in range(5))
    hf = np.abs(gray - ma).mean(axis=0)
    wgt = np.clip((hf - t0) / t1, 0, 1).astype(np.float32)
    wgt = cv2.GaussianBlur(wgt, (0, 0), 5) * strength
    before = float(hf.mean())

    # ROI 权重嵌回全幅 + 羽化蒙版
    wfull = np.zeros((h, w), np.float32)
    wfull[ry1:ry2, rx1:rx2] = wgt
    mask = np.zeros((h, w), np.float32)
    mask[ry1:ry2, rx1:rx2] = 1.0
    mask = cv2.GaussianBlur(mask, (0, 0), 9)
    w3 = (mask * wfull)[..., None]

    ext = [frames[min(max(i, 0), n - 1)] for i in range(-RADIUS, n + RADIUS)]
    out_frames = []
    for t in range(n):
        acc = np.zeros_like(frames[0], np.float32)
        for k in range(len(KER)):
            acc += ext[t + k].astype(np.float32) * KER[k]
        f32 = frames[t].astype(np.float32)
        fixed = f32 + w3 * (acc - f32)
        out_frames.append(np.clip(fixed, 0, 255).astype(np.uint8))

    after_gray = np.stack([cv2.cvtColor(f[ry1:ry2, rx1:rx2], cv2.COLOR_BGR2GRAY)
                           for f in out_frames]).astype(np.float32)
    write_frames_atomic(path, out_frames, fps)
    return dict(n=n, roi=roi, hf0=before, hf1=float(hf_of(after_gray)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--only", default="", help="只处理这些段名（逗号分隔），空=全部基础段")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--strength", type=float, default=0.9)
    ap.add_argument("--t0", type=float, default=0.5)
    ap.add_argument("--t1", type=float, default=1.5)
    args = ap.parse_args()
    seg_dir = Path(args.segments)
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    vids = [p for p in sorted(seg_dir.glob("*.mp4"))
            if p.suffix == ".mp4" and (not only or p.stem in only)]
    if not vids:
        raise SystemExit("没有匹配的段")
    for p in vids:
        r = process(p, args.strength, args.t0, args.t1)
        if r is None:
            print(f"  {p.name}: 跳过（脸检出不足/帧短）")
            continue
        print(f"  {p.name}: {r['n']}帧 嘴区hf {r['hf0']:.2f}→{r['hf1']:.2f} "
              f"roi={r['roi']}")


if __name__ == "__main__":
    main()
