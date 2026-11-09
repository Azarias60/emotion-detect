# -*- coding: utf-8 -*-
"""过渡切换流畅度自检（用户需求：跑完后自检过渡点切换流畅度）。

三个度量维度（全部对段目录实际存在的过渡逐对执行）：
  a. 交接帧差：源段末帧→过渡首帧（入口）、过渡末帧→目标段首帧（出口）。
     <6 流畅 / 6~12 可感 / >12 明显跳（与历史最好水平 5.24~7.50 对齐取 8 为门）；
  b. 过渡内部：尖峰/均值帧差 + 最小帧亮度（<15 判黑场，同 pick_best.py 判据）；
  c. 基础段动作量：除 greet 外各段帧差 mean（用户要求"几乎不动"，门 2.5——
     取自现有基线 idle1 2.27 的量级）与脸宽极差（稳定化验收线 <5%）。

--frames-out 会把每个过渡的首/中/尾帧与每对交接两帧抽成 PNG 横条，供 GLM 视觉核验。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/audit_transitions.py \
        --segments livetalking/guizi-character \
        --out runs/audit_transitions.json \
        --frames-out E:/ZCodeWorkspace/scratch/emotionDetect/guizi_trans_audit
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "livetalking"
for p in (str(ENGINE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

HANDOFF_GATE = 8.0     # 交接帧差门（历史修复后最好水平 5.24~7.50）
DARK_GATE = 15         # 黑场判定（最小帧亮度）
BASE_MEAN_GATE = 2.5   # 基础段帧差 mean 门（"几乎不动"）
SWING_GATE = 5.0       # 基础段脸宽极差门（稳定化验收线）


def load(p):
    cap = cv2.VideoCapture(str(p))
    fr = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        fr.append(f)
    cap.release()
    return fr


def d(a, b):
    return float(np.abs(a.astype(int) - b.astype(int)).mean())


def save_strip(frames, out: Path, w=176, h=320):
    out.parent.mkdir(parents=True, exist_ok=True)
    strip = np.hstack([cv2.resize(f, (w, h)) for f in frames])
    cv2.imwrite(str(out), strip)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", default="livetalking/guizi-character")
    ap.add_argument("--out", default="runs/audit_transitions.json")
    ap.add_argument("--frames-out", default="")
    args = ap.parse_args()

    seg_dir = Path(args.segments)
    cache = {}
    def seg(name):
        if name not in cache:
            cache[name] = load(seg_dir / f"{name}.mp4")
        return cache[name]

    base_names = [p.stem for p in sorted(seg_dir.glob("*.mp4")) if " to " not in p.stem]
    trans = [(p.stem,) for p in sorted(seg_dir.glob("*.mp4")) if " to " in p.stem]

    report = {"transitions": {}, "base_segments": {}, "gates": {
        "handoff": HANDOFF_GATE, "dark": DARK_GATE,
        "base_mean": BASE_MEAN_GATE, "swing": SWING_GATE}}
    fdir = Path(args.frames_out) if args.frames_out else None

    # ── 过渡逐对 ──
    for (stem,) in trans:
        a, b = stem.split(" to ")
        t = load(seg_dir / f"{stem}.mp4")
        sa, sb = seg(a), seg(b)
        n = len(t)
        adj = [d(t[i], t[i - 1]) for i in range(1, n)]
        entry = d(t[0], sa[-1])
        exit_ = d(t[-1], sb[0])
        peak = max(adj)
        mean = float(np.mean(adj))
        min_bright = float(min(f.mean() for f in t))
        dark = min_bright < DARK_GATE
        ok = entry <= HANDOFF_GATE and exit_ <= HANDOFF_GATE and not dark
        report["transitions"][stem] = {
            "entry": round(entry, 2), "exit": round(exit_, 2),
            "peak": round(peak, 2), "peak_at": int(np.argmax(adj)) + 1,
            "mean": round(mean, 2), "min_brightness": round(min_bright, 1),
            "dark": dark, "handoff_ok": ok,
        }
        if fdir:
            mid = n // 2
            save_strip([t[0], t[mid], t[-1], sa[-1], sb[0]],
                       fdir / f"{a}__{b}.png")
        print(f"{stem:20s} 入口 {entry:5.2f}  出口 {exit_:5.2f}  尖峰 {peak:5.2f}(f{int(np.argmax(adj))+1})  "
              f"均值 {mean:4.2f}  最暗 {min_bright:5.1f}  {'✓' if ok else '✗ 超门'}")

    # ── 基础段动作量 ──
    sys.path.insert(0, str(ROOT))
    from studio.qa_segments import load_detector
    det = load_detector()
    for name in base_names:
        fr = seg(name)
        n = len(fr)
        ws, means = [], []
        for f in fr:
            r = det.get_detections_for_batch(np.array([f]))
            if r and r[0] is not None:
                ws.append(float(r[0][2] - r[0][0]))
            means.append(d(f, fr[max(0, n - 1) - 0]) if False else 0)
        # 帧差 mean（灰度等价：用 RGB absdiff）
        means = [np.abs(fr[i].astype(int) - fr[i - 1].astype(int)).mean() for i in range(1, n)]
        mean = float(np.mean(means))
        v = np.array([x for x in ws if x])
        swing = 100 * (v.max() - v.min()) / v.mean() if len(v) > 3 else float("nan")
        swing = float(swing)
        greet = name.startswith("greet")
        ok = (mean <= BASE_MEAN_GATE or greet) and swing < SWING_GATE
        report["base_segments"][name] = {
            "frames": n, "diff_mean": round(mean, 2),
            "face_width_swing": round(swing, 1), "exempt": greet, "ok": ok,
        }
        if fdir:
            save_strip([fr[0], fr[n // 3], fr[2 * n // 3], fr[-1]],
                       fdir / f"base_{name}.png")
        print(f"{name:8s} 帧差mean {mean:4.2f}{'(greet豁免)' if greet else ''}  脸宽极差 {swing:4.1f}%  {'✓' if ok else '✗ 超门'}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n摘要已写 {args.out}" + (f"；抽帧图在 {fdir}" if fdir else ""))


if __name__ == "__main__":
    main()
