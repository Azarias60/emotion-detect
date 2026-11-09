# -*- coding: utf-8 -*-
"""用 LTX-2.3 以「本角色各段真实首尾帧」重熔全部过渡段（studio 第 3 道工序）。

背景（2026-10-18 审计发现）：guizi-character 里的 30 个过渡是 10 月 8 日给 zzf 角色
生成的旧片（人脸与 zzf 相关度 0.95+，与鬼子照片仅 0.25），状态机每次播过渡人物就
变成 zzf。本脚本用 v28 全套限定参数的真人版配方（ltx_transition_guizi.json：
STG 1.1 / cfg 2.5 / 位置锁定 / 57 帧 / guide 0.85）+ 鬼子基础段的真实端点重熔。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/regen_transitions.py \
        --segments livetalking/guizi-character --recipe ltx_transition_guizi.json --run guizi_t1

产出：段目录下 <A> to <B>.mp4 全部替换（原文件备份到 runs/<run>/backup/）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "livetalking"
for p in (str(ENGINE), str(ROOT / "generation")):
    if p not in sys.path:
        sys.path.insert(0, p)

import cv2
import numpy as np

import config
from comfy import api
from graphs import ltx_flf

FPS = 20
W, H = 704, 1280


def frames_of(video):
    cap = cv2.VideoCapture(str(video))
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(f)
    cap.release()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True, help="段目录（基础段 + 待替换过渡）")
    ap.add_argument("--recipe", default="ltx_transition_guizi.json")
    ap.add_argument("--run", default="guizi_t1")
    ap.add_argument("--seeds", type=int, nargs="+", default=None, help="覆盖配方的 seed 列表")
    ap.add_argument("--only", nargs="*", default=None, help="只重熔指定对，如 'idle1 to idle2'")
    args = ap.parse_args()

    seg_dir = Path(args.segments)
    recipe = json.loads((ROOT / "generation" / "recipes" / args.recipe).read_text(encoding="utf-8"))
    seeds = args.seeds or recipe["seeds"]
    run_dir = ROOT / "runs" / args.run
    backup = run_dir / "backup"
    run_dir.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=True)

    bases = sorted(p.stem for p in seg_dir.glob("*.mp4") if " to " not in p.stem)
    print(f"基础段: {bases}", flush=True)

    if args.only:
        # --only 允许自定义配对（含自过渡，如 talk to talk——说话中续播用）
        pairs = [tuple(o.split(" to ")) for o in args.only]
    else:
        pairs = [(a, b) for a in bases for b in bases if a != b]
    print(f"待重熔过渡: {len(pairs)} 对 × {len(seeds)} seed", flush=True)

    # 端点帧缓存（每段只抽一次）
    ep = {}
    for b in bases:
        fr = frames_of(seg_dir / f"{b}.mp4")
        ep[(b, "first")] = fr[0]
        ep[(b, "last")] = fr[-1]

    done, failed = 0, []
    for a, b in pairs:
        out_name = f"{a} to {b}.mp4"
        out_path = seg_dir / out_name
        if out_path.exists():
            shutil.copy2(out_path, backup / out_name)
        ea = run_dir / f"ep_{a}_last.png"
        eb = run_dir / f"ep_{b}_first.png"
        api.imwrite_u(ea, ep[(a, "last")])
        api.imwrite_u(eb, ep[(b, "first")])
        na, nb = api.upload_image(ea), api.upload_image(eb)

        best = None
        for seed in seeds:
            prefix = f"ltx23_{args.run}_{a}2{b}_s{seed}"
            graph = ltx_flf.build(na, nb, prefix, pos=recipe["pos"], neg=recipe["neg"],
                                  nag=recipe["neg_nag"], frames=recipe["frames"], seed=seed,
                                  guide=recipe["guide"], fps=FPS, width=W, height=H,
                                  stg_scale=recipe["stg_scale"], stg_blocks=recipe["stg_blocks"],
                                  cfg=recipe["cfg"])
            pid = api.submit(graph)
            print(f"[{a}→{b}] seed={seed} -> {pid}", flush=True)
            if not pid or not api.wait(pid, f"{a}→{b} s{seed}"):
                continue
            src = os.path.join(config.COMFY_OUTPUT, prefix + "_00001.mp4")
            fr = api.frames_of(src, min_frames=recipe["frames"] // 2)
            if len(fr) < recipe["frames"] // 2:
                print(f"[{a}→{b}] s{seed} 读帧失败（{len(fr)}）", flush=True)
                continue
            td = api.frame_diffs(fr)
            spike = max(td)
            print(f"[{a}→{b}] s{seed}: {len(fr)}帧 尖峰={spike:.2f}", flush=True)
            if best is None or spike < best[0]:
                best = (spike, src, fr)
        if best is None:
            failed.append(f"{a}→{b}")
            continue
        spike, src, fr = best
        shutil.copy2(src, out_path)
        print(f"[{a}→{b}] 选中尖峰 {spike:.2f} -> {out_name}", flush=True)
        done += 1

    print(f"\n完成 {done}/{len(pairs)}，失败 {len(failed)}: {failed}", flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
