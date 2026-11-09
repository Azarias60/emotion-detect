# -*- coding: utf-8 -*-
"""r1 产出自动筛选：idle1/idle3 各 3 seed 按【背景一致性+几乎不动+无异常动作】选优。

筛选门（全部来自本轮实测教训）：
  1. 背景店招尺度 vs greet 段 = 0.97~1.03（背景一致性，本轮返工的根因）；
  2. 脸宽 132~146（与其他段一致）且极差 <5%；
  3. 帧差 mean ≤2.5（"几乎不动"）；
  4. 动作曲线无持续异常段（连续 4 帧帧差 >7 = 有抬手类大动作）。

用法（引擎 venv）：python studio/pick_r1.py
"""
import glob
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path(r"E:\Comfyui\ComfyUI\No1基础包含文生图模型\ComfyUIportable\ComfyUIportable\ComfyUI\output\scail2")
STAGE = ROOT / "runs" / "r1_stage"
REF_SIGN = ROOT / "livetalking" / "guizi-character" / "greet.mp4"


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


def sign_scale(frame, tmpl, loc):
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    x, y = loc
    best = None
    for s in np.arange(0.80, 1.30, 0.02):
        sw, sh = int(84 * s), int(64 * s)
        sp = cv2.resize(tmpl, (sw, sh))
        if g.shape[0] < sh + 8 or g.shape[1] < sw + 8:
            continue
        res = cv2.matchTemplate(g, sp, cv2.TM_CCOEFF_NORMED)
        _, mx, _, _ = cv2.minMaxLoc(res)
        if best is None or mx > best[0]:
            best = (mx, s)
    return best


def main():
    sys.path.insert(0, str(ROOT / "livetalking"))
    from studio.qa_segments import load_detector
    det = load_detector()

    ref = load(REF_SIGN)
    g0 = cv2.cvtColor(ref[0], cv2.COLOR_BGR2GRAY)
    best_val, loc = -1, None
    for y in range(40, 260, 16):
        for x in range(380, 600, 16):
            v = g0[y:y + 64, x:x + 84].std()
            if v > best_val:
                best_val, loc = v, (x, y)
    tmpl = g0[loc[1]:loc[1] + 64, loc[0]:loc[0] + 84]

    STAGE.mkdir(parents=True, exist_ok=True)
    report = {}
    for seg in ("idle1", "idle3"):
        rows = []
        best = None
        for seed in ("s1", "s2", "s3"):
            src = OUT / f"guizi_r1_{seg}_{seed}_00001_.mp4"
            if not src.is_file():
                continue
            fr = load(src)
            n = len(fr)
            ws, diffs = [], []
            for i, f in enumerate(fr):
                r = det.get_detections_for_batch(np.array([f]))
                ws.append(float(r[0][2] - r[0][0]) if r and r[0] is not None else np.nan)
                if i:
                    diffs.append(float(np.abs(f.astype(int) - fr[i - 1].astype(int)).mean()))
            ws = np.array(ws)
            v = ws[~np.isnan(ws)]
            swing = 100 * (v.max() - v.min()) / v.mean()
            mean = float(np.mean(diffs))
            # 连续 4 帧帧差 >7 = 有大动作
            hot = sum(1 for i in range(len(diffs) - 3)
                      if all(d > 7 for d in diffs[i:i + 4]))
            _, scale = sign_scale(fr[0], tmpl, loc)
            bg_ok = 0.97 <= scale <= 1.03
            size_ok = 132 <= v.mean() <= 146
            quiet_ok = mean <= 2.5 and hot == 0
            score = abs(scale - 1.0) * 100 + swing * 0.3 + mean * 5 + hot * 20 + (0 if size_ok else 50)
            ok = bg_ok and size_ok and quiet_ok and swing < 5
            rows.append(f"  {seg} {seed}: {n}帧 背景{scale:.2f}{'✓' if bg_ok else '✗'} 脸宽{v.mean():.0f}"
                        f"{'✓' if size_ok else '✗'} 极差{swing:.1f}% 帧差{mean:.2f}{'✓' if mean <= 2.5 else '✗'}"
                        f" 大动作段{hot} {'✓ 全过' if ok else '✗'}")
            if ok or best is None or score < best[0]:
                best = (score, seed, src, ok)
        report[seg] = rows
        if best:
            shutil.copy2(best[2], STAGE / f"{seg}.mp4")
            rows.append(f"  >>> {seg} 选 {best[1]}{'（全过）' if best[3] else '（矮子拔将军，仍有残项）'} -> {STAGE / (seg + '.mp4')}")
    for seg, rows in report.items():
        print(f"\n[{seg}]")
        print("\n".join(rows))
    (ROOT / "runs" / "r1_stage" / "pick_report.json").write_text(
        json.dumps({seg: r for seg, r in report.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8")


if __name__ == "__main__":
    main()
