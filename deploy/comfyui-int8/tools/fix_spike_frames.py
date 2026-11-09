# -*- coding: utf-8 -*-
"""过渡段孤立尖峰帧修复（f1 实测教训的针对性工具）。

f1 重熔后 5 条涉及 greet/talk 的过渡有同一形态缺陷：入口/出口全流畅
（3.6~5.1），但中段某随机帧出现**孤立单帧跳变**（该帧帧差 20~25，其
前后帧差均 0~1）——LTX 对"挥手姿势↔垂手姿势"大差异插值的固有单帧崩坏，
换 seed 无法避免（历史重掷 20+ 次全部如此）。

修法：孤立尖峰帧 f（帧差>PEAK 且前后帧差均<CALM）用前后帧的加权中间帧
替换（0.5/0.5 线性混合即可——前后帧本身差异 <2，替换后帧差降到 ~12 的一半
即两侧各 ~10→视觉为轻微运动模糊）。逐段扫描修复所有孤立尖峰。

2026-11-08 扩展（e2egreet01 实测教训）：LTX 过渡还有两种形态——
① 尾部突发（连续 ≥3 帧帧差 9~21，收尾不收敛）；② 末帧跳变（最后一帧帧差 9+）。
换 seed 重掷 3 轮均无法避免（结构性）。新增 repair_jumps 通道：贪心摘除
跳变源帧（跳变差 >8 时移除跳变目标帧，直到最大帧差 ≤8 或达安全下限），
对孤立尖峰/双帧凸起/尾部突发/末帧跳变统一生效。

用法（引擎 venv）：
    livetalking/venv/Scripts/python.exe studio/fix_spike_frames.py \
        --segments livetalking/guizi-character [--apply]
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PEAK = 15.0   # 孤立尖峰阈值（帧差）
CALM = 5.0    # 前后帧必须平静的阈值（f1 实测上下文可到 3~5）


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


def fix_video(path: Path, apply: bool):
    fr, fps = load(path)
    n = len(fr)
    diffs = [d(fr[i], fr[i - 1]) for i in range(1, n)]
    drop_idx = set()
    for i in range(1, n):
        if diffs[i - 1] > PEAK:
            prev_ok = diffs[i - 2] < CALM if i >= 2 else True
            next_ok = diffs[i] < CALM if i < n - 1 else True
            if prev_ok and next_ok:
                # 丢弃尖峰帧：前后帧本身差异 <CALM，删除后内容连续无痕迹
                # （混合替换会留"鬼影"帧且尖峰只降一半；丢一帧 0.05s 无感）
                drop_idx.add(i)
    if not drop_idx:
        return None
    new = [f for i, f in enumerate(fr) if i not in drop_idx]
    diffs2 = [d(new[i], new[i - 1]) for i in range(1, len(new))]
    peak_before = max(diffs)
    peak_after = max(diffs2)
    print(f"{path.name:22s} 丢弃 {len(drop_idx)} 帧（f{sorted(drop_idx)}） {n}->{len(new)}帧 "
          f"尖峰 {peak_before:.1f} -> {peak_after:.1f}"
          + ("  [已写回]" if apply else "  [dry-run]"))
    if apply:
        save(path, new, fps)
    return {"seg": path.stem, "dropped": sorted(drop_idx), "frames": [n, len(new)],
            "peak_before": round(peak_before, 2), "peak_after": round(peak_after, 2)}


def repair_jumps(path: Path, apply: bool, gate: float = 8.0, cap: int = 20):
    """贪心平滑跳变：最大帧差 > gate 时处理跳变处并重测。
    铁律：末帧（= 下一动作段的首帧端点）永不摘除——摘掉它过渡就不收敛，
    会硬切到下一段（e2egreet01 实测教训：砍端点导致全段闪切）。
    跳变目标为末帧时改为混合末两帧（逐渐收敛）；常规跳变摘除目标帧。"""
    fr, fps = load(path)
    n0 = len(fr)
    ops = []
    for _ in range(cap):
        n = len(fr)
        if n < max(40, int(n0 * 0.55)):
            break  # 安全下限：最多摘除 45% 帧
        g = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in fr]
        d = [float(np.abs(g[i + 1] - g[i]).mean()) for i in range(n - 1)]
        if not d or max(d) <= gate:
            break
        i = int(np.argmax(d))          # 跳变发生在 fr[i] -> fr[i+1]
        if i + 1 == n - 1:
            # 跳变目标是末帧：混合末两帧（不丢端点，逐渐收敛）
            fr[-1] = ((fr[-2].astype(np.float32) + fr[-1].astype(np.float32)) / 2).astype(np.uint8)
            ops.append("blend-end")
        else:
            del fr[i + 1]              # 常规：丢跳变的目标帧
            ops.append("drop%d" % (i + 1))
    if not ops:
        return None
    d2 = [float(np.abs(cv2.cvtColor(fr[i + 1], cv2.COLOR_BGR2GRAY).astype(np.float32)
                       - cv2.cvtColor(fr[i], cv2.COLOR_BGR2GRAY).astype(np.float32)).mean())
          for i in range(len(fr) - 1)]
    print(f"{path.name:22s} {'+'.join(ops[:6])}{'…' if len(ops) > 6 else ''} "
          f"{n0}->{len(fr)}帧 最大帧差 -> {max(d2):.1f}" + ("  [已写回]" if apply else "  [dry-run]"))
    if apply:
        save(path, fr, fps)
    return {"seg": path.stem, "ops": ops, "peak_after": round(max(d2), 2)}
    d2 = [float(np.abs(cv2.cvtColor(fr[i + 1], cv2.COLOR_BGR2GRAY).astype(np.float32)
                       - cv2.cvtColor(fr[i], cv2.COLOR_BGR2GRAY).astype(np.float32)).mean())
          for i in range(len(fr) - 1)]
    print(f"{path.name:22s} 摘除 {len(dropped)} 帧（f{sorted(dropped)}） {n0}->{len(fr)}帧 "
          f"最大帧差 -> {max(d2):.1f}" + ("  [已写回]" if apply else "  [dry-run]"))
    if apply:
        save(path, fr, fps)
    return {"seg": path.stem, "dropped": sorted(dropped), "peak_after": round(max(d2), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    seg_dir = Path(args.segments)
    for v in sorted(seg_dir.glob("*.mp4")):
        if " to " in v.stem:
            fix_video(v, args.apply)
    if args.apply:
        for v in sorted(seg_dir.glob("*.mp4")):
            if " to " in v.stem:
                repair_jumps(v, args.apply)


if __name__ == "__main__":
    main()
