# -*- coding: utf-8 -*-
"""过渡段亮度/对比度匹配后处理（studio 兜底工序，正常情况下不用）。

LTX 首尾帧引导只锚定 frame 0/-1 两帧，中段由模型自由生成。真人实拍端点
（鬼子：端点均值亮度 ~54、通道中位 ~30 的暗灰房间背景）配 v28 的
"flat and dim" 灯光描写时，模型自生成帧均值只有参考一半（~26），且**通道
中位数掉到 1（近纯黑背景）**——这是曝光+对比度的双重差异，不是单纯变暗，
全局增益修不了（修背景会炸脸）。

配方 v2 已把灯光描写改成"与参考帧完全一致"，本脚本是配方修不掉时的第二道
保险：对中段做逐通道**均值/标准差线性匹配**（matched = f*gain + offset），
把中段的均值/std 精确映射到 guide 锚定帧的水平。线性映射时序稳定，中段均值
对齐后边界天然平滑，不需要斜坡。

用法（引擎 venv）：
    livetalking/venv/Scripts/python.exe studio/fix_brightness.py \
        --segments livetalking/guizi-character --report
    # 确认报告无误后加 --apply 覆盖写回
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

EDGE = 3           # guide 锚定区帧数（首/末各 3 帧）
GAP_TRIGGER = 4    # 段内 guide/中段亮度差触发线（v1 产出 27，真实素材 ~0）
HEAD = 8           # 帧差尖峰告警线（3D 库已上线过渡实测 6.25）


def read_all(video):
    cap = cv2.VideoCapture(str(video))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    cap.release()
    return frames, fps


def write_all(video, frames, fps):
    h, w = frames[0].shape[:2]
    tmp = video.with_suffix(".tmp.mp4")
    out = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in frames:
        out.write(f)
    out.release()
    shutil.move(str(tmp), str(video))


def match_hist_frame(src, ref_cdf, ref_values):
    """逐通道直方图匹配：把 src 的亮度分布映射到 ref 的分布。

    线性均值/std 匹配对不了"双峰分布"——LTX 暗背景帧是 [纯黑背景 + 主体]
    两个峰，参考帧是 [暗灰背景 + 主体]，一条直线对不上两个峰。CDF 匹配
    能把背景峰和主体峰分别对齐，是这种情况唯一正确的映射。
    """
    out = np.empty_like(src)
    flat = src.reshape(-1, 3)
    res = np.empty_like(flat)
    for c in range(3):
        vals, inv, counts = np.unique(flat[:, c], return_inverse=True, return_counts=True)
        cdf = np.cumsum(counts).astype(np.float64)
        cdf /= cdf[-1]
        res[:, c] = np.interp(cdf, ref_cdf[c], ref_values[c])[inv]
    return res.reshape(src.shape)


def fix_video(video, apply, verbose=True):
    frames, fps = read_all(video)
    n = len(frames)
    if n < 2 * EDGE + 2:
        return None
    head = frames[:EDGE]
    tail = frames[n - EDGE:]
    mid = frames[EDGE:n - EDGE]
    # 触发条件：段内 guide 帧与中段的亮度差。v1 配方产出差 27（54 vs 26）；
    # 内部一致的真实素材差 ~0，不动它（拿外部参考去"修"一致素材会修出 pop）。
    ref_mean = float(np.mean([f.mean() for f in head + tail]))
    cur_mean = float(np.mean([f.mean() for f in mid]))
    gap = ref_mean - cur_mean
    if abs(gap) < GAP_TRIGGER:
        if verbose:
            print(f"{video.name:<20} 段内差 {gap:+.1f} 无需处理（帧差基准 "
                  f"{float(np.mean([np.abs(frames[i].astype(int) - frames[i-1].astype(int)).mean() for i in range(1, n)])):.2f}）")
        return None
    if abs(gap) > 25:
        if verbose:
            print(f"{video.name:<20} 段内差 {gap:+.1f} 过大，放弃自动修复（重新生成更稳）")
        return None

    # 参考分布 = guide 锚定帧合并的逐通道 CDF
    ref = np.concatenate([np.stack(head).reshape(-1, 3), np.stack(tail).reshape(-1, 3)])
    ref_cdf, ref_values = [], []
    for c in range(3):
        vals, counts = np.unique(ref[:, c], return_counts=True)
        cdf = np.cumsum(counts).astype(np.float64)
        ref_cdf.append(cdf / cdf[-1])
        ref_values.append(vals.astype(np.float64))

    def diff_of(seq):
        return float(np.mean([np.abs(seq[i].astype(int) - seq[i - 1].astype(int)).mean()
                              for i in range(1, len(seq))]))
    before = diff_of(frames)
    fixed = list(head) + [match_hist_frame(f, ref_cdf, ref_values) for f in mid] + list(tail)
    after = diff_of(fixed)
    peak = max(np.abs(fixed[i].astype(int) - fixed[i - 1].astype(int)).mean()
               for i in range(1, n))
    mark = "" if peak < HEAD else f" ⚠尖峰 {peak:.1f}"
    if verbose:
        print(f"{video.name:<20} 段内差 {gap:+.1f} 帧差 {before:.2f}→{after:.2f}{mark}"
              + ("  [已写回]" if apply else "  [dry-run]"))
    if apply:
        write_all(video, fixed, fps)
    return {"seg": video.stem, "gap": round(gap, 1), "spike_before": round(before, 2),
            "spike_after": round(after, 2), "peak_after": round(peak, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--apply", action="store_true", help="覆盖写回（默认只报告）")
    ap.add_argument("--all", action="store_true",
                    help="也处理基础段（默认只处理名字含 ' to ' 的过渡段——"
                         "基础段有真实动作位移，匹配会把动作尖峰一起放大）")
    ap.add_argument("--report", default="")
    args = ap.parse_args()
    seg_dir = Path(args.segments)
    videos = [v for v in sorted(seg_dir.glob("*.mp4")) if args.all or " to " in v.stem]
    results = [r for r in (fix_video(v, args.apply) for v in videos) if r]
    if args.report and results:
        Path(args.report).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        print("报告已写:", args.report)


if __name__ == "__main__":
    main()
