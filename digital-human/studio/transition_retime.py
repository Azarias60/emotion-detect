# -*- coding: utf-8 -*-
"""过渡段冻结保持修剪（LTX 提前到姿后原地定住 → 砍掉静止段）。

缺陷形态（ly 2026-11-04 实测）：LTX 在过渡前段完成姿势迁移后，剩余帧
长时间静止（最长 31 连帧，帧间差 <0.8），播放即"人物定住不动"。
morph_fix 修的是单帧瞬移；本工具修的是连续静止——直接剪掉静止帧：
- 尾部静止：整段剪掉（过渡变短，20fps 下 1~2s 完全够用）
- 中段静止：剪掉静止区间的中间帧（保留首尾各一帧衔接，动作轻微加速）

用法：livetalking/venv/Scripts/python.exe studio/transition_retime.py \
    --segments <dir> [--apply] [--min-run 14] [--th 0.8]
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

TH = 0.8
MIN_RUN = 14
TAIL_SPIKE = 12.0   # 末帧与前一帧差超过此值：末帧是异常落点，剪掉
TAIL_MAX_DROP = 3


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


def frozen_runs(ds, th, min_run):
    """静止区间列表 [(lo, hi)]：ds[lo]..ds[hi] 连续 < th（对应帧 lo+1..hi+1）。"""
    runs = []
    lo = None
    for i, v in enumerate(ds):
        if v < th:
            if lo is None:
                lo = i
        else:
            if lo is not None and i - lo + 1 >= min_run:
                runs.append((lo, i))
            lo = None
    if lo is not None and len(ds) - lo + 1 >= min_run:
        runs.append((lo, len(ds)))
    return runs


def retime_video(path: Path, apply: bool, th: float, min_run: int, tail_spike: float,
                 tail_max: int, landing: np.ndarray | None = None,
                 start: np.ndarray | None = None):
    fr, fps = load(path)
    n = len(fr)
    ds = [d(fr[i], fr[i - 1]) for i in range(1, n)]
    runs = frozen_runs(ds, th, min_run)
    # 尾帧异常落点：静止修剪后，最后一帧可能跳变（morph 无法修末帧边界）
    tail_dropped = 0
    while ds and ds[-1] > tail_spike and tail_dropped < tail_max and n - tail_dropped > 10:
        tail_dropped += 1
        ds.pop()
    if tail_dropped:
        print(f"{path.name:22s} 末帧异常落点，剪掉最后 {tail_dropped} 帧（差 {ds[-1] if False else ''}"
              f"{'>' + str(tail_spike)}）")
    keep_mask = [True] * n
    n_cut = 0
    for (lo, hi) in runs:
        # ds[lo..hi] 对应帧 lo+1..hi+1；保留帧 lo+1（动作终点）作落点
        tail = hi >= len(ds) - 1  # 静止延伸到最后：其后所有帧（含尖峰尾帧）全剪
        if tail:
            for f in range(lo + 2, n):
                if keep_mask[f]:
                    keep_mask[f] = False
                    n_cut += 1
        else:
            for f in range(lo + 2, hi + 2):
                if keep_mask[f]:
                    keep_mask[f] = False
                    n_cut += 1
    new = [fr[i] for i, k in enumerate(keep_mask[: n - tail_dropped]) if k]
    # 入点锚定：开头 K 帧渐进混合到 A 状态末帧，首帧=A末帧本身——进过渡零跳变
    start_note = ""
    if start is not None and len(new) >= 10:
        gap_s = d(new[0], start)
        Ks = min(len(new) // 2, max(5, int(gap_s / 2.0) + 2))
        if gap_s > 1.5 and Ks >= 3:
            h, w = new[0].shape[:2]
            st = cv2.resize(start, (w, h))
            for k in range(Ks):
                alpha = 1 - (k + 1) / Ks
                new[k] = cv2.addWeighted(new[k], 1 - alpha, st, alpha, 0)
            start_note = f" + 入点锚定({Ks}帧,差距{gap_s:.1f})"
    # 落点锚定：末尾 K 帧渐进混合到目标状态首帧，末帧=落点帧本身
    # ——过渡切进 B 状态时零跳变（由构造保证）
    blend_note = ""
    if landing is not None and len(new) >= 10:
        # 自适应混合窗：按末帧与落点的差距决定混合帧数（每帧变化 ≈2 以内），
        # 末帧=落点帧本身，过渡切进 B 状态零跳变（由构造保证）
        gap = d(new[-1], landing)
        K = min(len(new) // 2, max(5, int(gap / 2.0) + 2))
        if gap > 1.5 and K >= 3:
            h, w = new[-1].shape[:2]
            land = cv2.resize(landing, (w, h))
            for k in range(K):
                alpha = (k + 1) / K
                new[-K + k] = cv2.addWeighted(new[-K + k], 1 - alpha, land, alpha, 0)
            blend_note = f" + 落点锚定({K}帧,差距{gap:.1f})"
    changed = n_cut > 0 or tail_dropped > 0 or bool(start_note) or bool(blend_note)
    if not changed:
        return None
    new_ds = [d(new[i], new[i - 1]) for i in range(1, len(new))]
    peak_after = max(new_ds) if new_ds else 0.0
    cut_note = f"剪 {n_cut} 帧静止" + (f" + 尾帧{tail_dropped}个" if tail_dropped else "") + start_note + blend_note
    print(f"{path.name:22s} {n}->{len(new)}帧（{cut_note}）最差帧间差 {peak_after:.2f}"
          + ("  [已写回]" if apply else "  [dry-run]"))
    if apply:
        save(path, new, fps)
    return {"seg": path.stem, "cut": n_cut, "after_peak": round(peak_after, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-run", type=int, default=MIN_RUN)
    ap.add_argument("--th", type=float, default=TH)
    ap.add_argument("--tail-spike", type=float, default=TAIL_SPIKE)
    ap.add_argument("--tail-max", type=int, default=TAIL_MAX_DROP)
    ap.add_argument("--landings", default="",
                    help="落点锚定映射: 过渡名=状态名[;...], 状态名取同目录 <状态>.mp4 首帧")
    ap.add_argument("--starts", default="",
                    help="入点锚定映射: 过渡名=状态名[;...], 状态名取同目录 <状态>.mp4 末帧")
    args = ap.parse_args()
    land_map: dict[str, str] = {}
    for item in filter(None, args.landings.split(";")):
        tname, sname = item.split("=", 1)
        land_map[tname.strip()] = sname.strip()
    start_map: dict[str, str] = {}
    for item in filter(None, args.starts.split(";")):
        tname, sname = item.split("=", 1)
        start_map[tname.strip()] = sname.strip()
    land_cache: dict[str, np.ndarray] = {}
    start_cache: dict[str, np.ndarray] = {}

    def state_frame(sname: str, last: bool) -> np.ndarray:
        fr = load(Path(args.segments) / f"{sname}.mp4")[0]
        return fr[-1] if last else fr[0]

    for v in sorted(Path(args.segments).glob("*.mp4")):
        if " to " not in v.stem:
            continue
        landing = None
        start = None
        sname = land_map.get(v.stem)
        if sname:
            if sname not in land_cache:
                land_cache[sname] = state_frame(sname, last=False)
            landing = land_cache[sname]
        sname = start_map.get(v.stem)
        if sname:
            if sname not in start_cache:
                start_cache[sname] = state_frame(sname, last=True)
            start = start_cache[sname]
        retime_video(v, args.apply, args.th, args.min_run, args.tail_spike,
                     args.tail_max, landing, start)


if __name__ == "__main__":
    main()
