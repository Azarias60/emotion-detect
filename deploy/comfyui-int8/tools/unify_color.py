# -*- coding: utf-8 -*-
"""两段式全段肤色统一（v2：整帧 LAB + 脸部标量微调，均不会发散）。

v1 教训：
- 纯整帧 LAB 对齐：稳定收敛（残差 0.3~0.9），但统计被背景主导，
  脸部仍残留 -5~-9 偏差（用户看的就是脸，不够）；
- 纯脸部 LAB 对齐：std 比值发散（过渡段脸盒常拍到背景，统计失真，
  变换后偏差反而 50+）——已回滚。

v2 两段式（每一级都只有安全操作）：
  第一级  整帧 Reinhard LAB：把每段整体色调/对比度对齐到基础段联合统计；
  第二级  脸部标量 offset：测每段脸部 BGR 均值与目标的差，作为**常量偏移**
          加到整帧（不缩放、不发散，最多 ±12）；脸部严格对齐，背景跟随微调。

用法（引擎 venv）：
    livetalking/venv/Scripts/python.exe studio/unify_color.py \
        --segments livetalking/guizi-character [--apply]
"""
import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "livetalking"
for p in (str(ENGINE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

BASE_NAMES = ("idle1", "idle2", "idle3", "greet", "talk", "talk2")
MAX_OFFSET = 12.0  # 脸部微调的单通道上限（超过说明异常，clip 住）


def load(p):
    cap = cv2.VideoCapture(str(p))
    fr = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        fr.append(f)
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    cap.release()
    return fr, fps


def save(p, fr, fps):
    h, w = fr[0].shape[:2]
    tmp = Path(str(p) + ".tmp.mp4")
    out = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in fr:
        out.write(f)
    out.release()
    tmp.replace(p)


def lab_stats(frames, idxs):
    labs = [cv2.cvtColor(frames[i], cv2.COLOR_BGR2LAB).astype(np.float32) for i in idxs]
    pix = np.concatenate([l.reshape(-1, 3) for l in labs])
    return pix.mean(axis=0), pix.std(axis=0)


def transfer(frame, sm, ss, tm, ts):
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB).astype(np.float32)
    out = (lab - sm) * (ts / np.maximum(ss, 1e-3)) + tm
    return cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def face_mean(frames, det):
    """采样帧脸部 BGR 均值（检不出的帧跳过）。"""
    idxs = np.linspace(0, len(frames) - 1, min(5, len(frames))).astype(int)
    ms = []
    for i in idxs:
        r = det.get_detections_for_batch(np.array([frames[i]]))
        if r and r[0] is not None:
            x1, y1, x2, y2 = [int(v) for v in r[0]]
            ms.append(frames[i][y1:y2, x1:x2].reshape(-1, 3).mean(axis=0))
    return np.mean(ms, axis=0) if ms else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    seg_dir = Path(args.segments)

    from studio.qa_segments import load_detector
    det = load_detector()

    # 目标：基础段联合（整帧 LAB + 脸部 BGR）
    lab_tgt, face_tgt = [], []
    for name in BASE_NAMES:
        f = seg_dir / f"{name}.mp4"
        if not f.is_file():
            continue  # 单段/裁剪模式：目标统计只统计实际存在的基础段
        fr, _ = load(f)
        idxs = np.linspace(0, len(fr) - 1, min(6, len(fr))).astype(int)
        lab_tgt.append(lab_stats(fr, idxs))
        fm = face_mean(fr, det)
        if fm is not None:
            face_tgt.append(fm)
    if not lab_tgt:
        raise SystemExit("段目录里没有任何基础段可做目标统计（unify_color 需至少 1 段）")
    tgt_mean = np.mean([m for m, _ in lab_tgt], axis=0)
    tgt_std = np.mean([s for _, s in lab_tgt], axis=0)
    face_base = np.mean(face_tgt, axis=0)
    print(f"目标：整帧LAB mean={tgt_mean.round(1)} | 脸部BGR={face_base.round(1)}")

    for v in sorted(seg_dir.glob("*.mp4")):
        fr, fps = load(v)
        n = len(fr)
        idxs = np.linspace(0, n - 1, min(6, n)).astype(int)
        sm, ss = lab_stats(fr, idxs)
        # 第一级：整帧 LAB 对齐
        out = [transfer(f, sm, ss, tgt_mean, tgt_std) for f in fr]
        # 第二级：脸部标量 offset（常量、clip ±MAX_OFFSET，不发散）
        fm = face_mean(out, det)
        offset = np.zeros(3)
        if fm is not None:
            offset = np.clip(face_base - fm, -MAX_OFFSET, MAX_OFFSET)
        if np.abs(offset).max() >= 0.8:
            out = [np.clip(f.astype(np.float32) + offset, 0, 255).astype(np.uint8) for f in out]
        fm2 = face_mean(out, det)
        resid = float(np.abs(fm2 - face_base).max()) if fm2 is not None else -1
        print(f"{v.name:22s} 整帧Δ{np.linalg.norm(sm - tgt_mean):4.1f} 脸offset[{offset[0]:+.1f} {offset[1]:+.1f} {offset[2]:+.1f}] 残差{resid:4.1f}"
              + ("  [已写回]" if args.apply else "  [dry-run]"))
        if args.apply and (np.abs(offset).max() >= 0.8 or np.linalg.norm(sm - tgt_mean) >= 2.0):
            bk = seg_dir / f"_backup_{v.stem}"
            if not bk.exists():
                bk.mkdir()
                shutil.copy2(v, bk / v.name)
            save(v, out, fps)
    print("完成")


if __name__ == "__main__":
    main()
