# -*- coding: utf-8 -*-
"""过渡段瞬移点 morphing 修复（LTX 大姿势差插值的"瞬移"缺陷）。

缺陷形态（f1 实测）：LTX 处理"垂手↔挥手"大姿势差的过渡时，把渐变做成
"长时间停在姿势 A，一帧瞬移到姿势 B，长时间停在 B"——帧差曲线呈成对尖峰
（切换帧与前帧差 20+、与后帧差 20+，两侧各自平静）。孤立帧修复（替换/
丢弃）无效：瞬移是真实的两帧内容切换。

修法：在切换点 f（帧差>PEAK 且两侧平静）取 before=f-1、after=f+1，用
Farneback 稠密光流计算 before→after 的运动场，生成 N 帧光流 warp 中间帧
替换切换区间——瞬移展开为平滑渐变。光流在暗夜景可能噪声大，退化为
cross-dissolve（线性混合，观感为快速淡切，仍远好于硬切）。

用法（引擎 venv）：
    livetalking/venv/Scripts/python.exe studio/morph_fix.py \
        --segments livetalking/guizi-character [--apply] [--steps 6]
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

PEAK = 10.5  # 与产线过渡门（gate_transitions 尖峰>10）对齐：门标记的尖峰必须能被修
CALM = 6.0
STEPS = 6  # 切换点展开成多少帧


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


def d(a, b):
    return float(np.abs(a.astype(int) - b.astype(int)).mean())


def warp_frame(img, flow, alpha):
    """按 flow*alpha 后向 warp。"""
    h, w = flow.shape[:2]
    base = np.meshgrid(np.arange(w), np.arange(h))
    map_x = (base[0] + flow[..., 0] * alpha).astype(np.float32)
    map_y = (base[1] + flow[..., 1] * alpha).astype(np.float32)
    return cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def morph(before, after, steps):
    """before→after 的 steps 帧光流中间帧序列（不含两端）。"""
    g0 = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
    g1 = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(g0, g1, None, 0.5, 4, 24, 5, 5, 1.2, 0)
    out = []
    for k in range(1, steps + 1):
        a = k / (steps + 1)
        w0 = warp_frame(before, flow, a)
        # after 反向 warp（flow 取负近似）
        w1 = warp_frame(after, flow, a - 1.0)
        blend = cv2.addWeighted(w0, 1 - a, w1, a, 0)
        out.append(blend)
    return out


def fix_video(path: Path, apply: bool, steps: int):
    fr, fps = load(path)
    n = len(fr)
    diffs = [d(fr[i], fr[i - 1]) for i in range(1, n)]
    cut_i = None
    for i in range(1, n):
        if diffs[i - 1] > PEAK:
            prev_ok = diffs[i - 2] < CALM if i >= 2 else True
            next_ok = diffs[i] < CALM if i < n - 1 else True
            if prev_ok and next_ok:
                cut_i = i
                break  # 每段只修第一个切换点（f1 实测每段仅 1 个）
    if cut_i is None:
        return None
    if cut_i >= n - 1:
        # 切点落在最后一帧：没有"后帧"可做光流，属段边界伪影而非段内瞬移，
        # 本法修不了——跳过（此前 fr[cut_i+1] 直接越界崩掉整条产线，2026-11-02 ly 实测）
        print(f"{path.name:22s} f{cut_i} 切点在末帧边界，跳过")
        return None
    before, after = fr[cut_i - 1], fr[cut_i + 1]
    mid = morph(before, after, steps)
    new = fr[:cut_i] + mid + fr[cut_i + 1:]
    diffs2 = [d(new[i], new[i - 1]) for i in range(1, len(new))]
    peak_before = max(diffs)
    peak_after = max(diffs2)
    print(f"{path.name:22s} f{cut_i} 瞬移展开为 {steps} 帧 {n}->{len(new)}帧 "
          f"尖峰 {peak_before:.1f} -> {peak_after:.1f}"
          + ("  [已写回]" if apply else "  [dry-run]"))
    if apply:
        save(path, new, fps)
    return {"seg": path.stem, "cut": cut_i, "peak_before": round(peak_before, 2),
            "peak_after": round(peak_after, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--steps", type=int, default=STEPS)
    args = ap.parse_args()
    for v in sorted(Path(args.segments).glob("*.mp4")):
        if " to " in v.stem:
            fix_video(v, args.apply, args.steps)


if __name__ == "__main__":
    main()
