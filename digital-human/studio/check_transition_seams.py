# -*- coding: utf-8 -*-
"""过渡段接缝体检：量化每条 "A to B" 在**切换瞬间**的帧间突变，供重掷判定。

背景（2026-11-13 用户原则）：丝滑衔接必须靠**生成质量**，禁止对素材做任何
像素处理（交叉淡化/颜色增益一律不许）。所以衔接问题唯一合法解法是——
**检测出不合格的过渡段，换 seed 重新生成**。

判据：把「A 段末帧 → 过渡首帧」和「过渡尾帧 → B 段首帧」的逐像素差，
与过渡段内部相邻帧差的中位数比较。接缝处应当和段内运动同量级；
接缝差 >> 段内中位差，说明这一段的起手/收尾与基础段对不上（换模型重掷）。

用法：
    python studio/check_transition_seams.py --segments runs/make_avatar/<name>/segments
    python studio/check_transition_seams.py --segments ... --ratio 3.0 --absolute 3.0

判定：接缝差 < absolute，或 < ratio × 段内中位差 → PASS；否则 FAIL（该重掷）。
退出码：全 PASS 为 0，有 FAIL 为 1（可直接当产线闸门用）。
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

BASE_NAMES = ("idle1", "idle2", "idle3", "greet", "talk", "comfort", "encourage")


def read_frames(path: Path) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--ratio", type=float, default=3.0,
                    help="接缝差超过段内中位差的多少倍判 FAIL")
    ap.add_argument("--absolute", type=float, default=3.0,
                    help="接缝差绝对值上限（0-255 尺度）")
    args = ap.parse_args()

    seg_dir = Path(args.segments)
    base = {}
    for name in BASE_NAMES:
        p = seg_dir / f"{name}.mp4"
        if p.is_file():
            fr = read_frames(p)
            if fr:
                base[name] = (fr[0], fr[-1])

    rows, fails = [], []
    for p in sorted(seg_dir.glob("* to *.mp4")):
        src, dst = p.stem.split(" to ")
        if src not in base or dst not in base:
            continue
        frames = read_frames(p)
        if len(frames) < 5:
            continue
        inner = [float(cv2.absdiff(frames[i], frames[i - 1]).mean())
                 for i in range(1, len(frames))]
        med = float(np.median(inner))
        seam_in = float(cv2.absdiff(frames[0], base[src][1]).mean())
        seam_out = float(cv2.absdiff(frames[-1], base[dst][0]).mean())
        limit = max(args.absolute, med * args.ratio)
        ok = seam_in <= limit and seam_out <= limit
        rows.append((p.stem, med, seam_in, seam_out, limit, ok))
        if not ok:
            fails.append(p.stem)

    print(f"{'过渡段':18s}{'段内中位':>9s}{'入口接缝':>10s}{'出口接缝':>10s}{'阈值':>8s}  判定")
    print("-" * 68)
    for name, med, si, so, limit, ok in rows:
        print(f"{name:18s}{med:9.2f}{si:10.2f}{so:10.2f}{limit:8.2f}  {'PASS' if ok else 'FAIL ← 需重掷'}")
    print("-" * 68)
    print(f"通过 {len(rows) - len(fails)} / {len(rows)}")
    if fails:
        print("待重掷：")
        for name in fails:
            print(f"  {name}")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
