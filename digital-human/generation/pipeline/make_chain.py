# -*- coding: utf-8 -*-
"""v28 三段链条：idle1 →(LTX-2.3 首尾帧过渡)→ greet。

全程 704x1280@20fps 原生输出，不做任何归一化/缩放/帧率转换。

流程：
  [1] SCAIL-2 动作迁移 greet 姿态视频（用户手裁源 + 反抖动提示词）→ greet 成片段
      （可选）同法迁移 idle 姿态视频 → idle 成片段
  [2] 抽端点帧（idle 末帧 + greet 首帧）→ 上传 ComfyUI input
  [3] LTX-2.3 首尾帧生成过渡：多 seed 并行提交前的串行排队，按「单帧尖峰最小」选优
  [4] ffmpeg 无损拼接（-c copy）+ 交付目录落盘 + QA 拼图

用法（项目根目录，python 用 E:\\ZCodeWorkspace\\venv\\Scripts\\python.exe）：
  python -m pipeline.make_chain                      # 用 assets/pose 下的默认素材复跑 v28
  python -m pipeline.make_chain --greet-pose D:\\xxx.mp4 --run v29
  python -m pipeline.make_chain --idle output里的idle.mp4 --greet-pose 新姿态.mp4
  python -m pipeline.make_chain --greet 已有greet.mp4 --idle 已有idle.mp4   # 只重跑过渡+拼接
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

import config
from comfy import api
from graphs import ltx_flf, scail


def load_recipes(scail_recipe="scail_antijitter_v28.json"):
    with open(os.path.join(config.RECIPES_DIR, "ltx_transition_v28.json"), encoding="utf-8") as f:
        ltx = json.load(f)
    with open(os.path.join(config.RECIPES_DIR, scail_recipe), encoding="utf-8") as f:
        sc = json.load(f)
    return ltx, sc


def run_scail(which, pose_path, run, cap=None, sc_recipe=None, ref_image=None):
    """SCAIL-2 迁移一段姿态视频，返回成片路径。"""
    sc_recipe = sc_recipe or load_recipes()[1]
    name = api.ensure_input_file(pose_path)
    if ref_image:
        ref_image = api.ensure_input_file(ref_image)
    if cap is None:
        local = pose_path if os.path.exists(pose_path) else os.path.join(config.COMFY_INPUT, name)
        cap = api.video_info(local)[0]
    prefix = f"scail2/{run}_{which}"
    p = scail.build(which, pose_video=name, cap=cap, prefix=prefix,
                    prompt=sc_recipe["prompt"], negative=sc_recipe.get("negative", ""),
                    force_rate=sc_recipe.get("force_rate", 20), fps=sc_recipe.get("fps", 20),
                    ref_image=ref_image)
    pid = api.submit(p)
    print(f"SCAIL {which}（{name} {cap}帧 @{sc_recipe.get('force_rate', 20)}fps）-> {pid}", flush=True)
    if not pid or not api.wait(pid, f"SCAIL {which}"):
        sys.exit(f"[FAIL] SCAIL {which} 生成失败")
    out = scail.output_path(prefix)
    fr = api.frames_of(out)
    if len(fr) < 10:
        sys.exit(f"[FAIL] {out} 读帧失败（{len(fr)} 帧）")
    print(f"SCAIL {which} 完成: {out}（{len(fr)} 帧）", flush=True)
    return out, fr


def run_ltx_transition(idle_png, greet_png, run, ltx, seeds, frames, guide):
    """多 seed 生成过渡段，按单帧尖峰最小选优。返回 (路径, 帧列表, 指标)。"""
    results = []
    for seed in seeds:
        prefix = f"ltx23_{run}_s{seed}"
        p = ltx_flf.build(idle_png, greet_png, prefix, pos=ltx["pos"], neg=ltx["neg"],
                          nag=ltx["neg_nag"], frames=frames, seed=seed, guide=guide,
                          fps=config.FPS, width=config.W, height=config.H)
        pid = api.submit(p)
        print(f"LTX 过渡 seed={seed} -> {pid}", flush=True)
        if not pid or not api.wait(pid, f"s{seed}"):
            continue
        src = os.path.join(config.COMFY_OUTPUT, prefix + "_00001.mp4")
        fr = api.frames_of(src, min_frames=frames // 2)
        if len(fr) < frames // 2:
            print(f"s{seed}: 读帧失败（{len(fr)} 帧）", flush=True)
            continue
        td = api.frame_diffs(fr)
        spike, tail10 = max(td), float(np.mean(td[-10:]))
        results.append({"seed": seed, "src": src, "frames": fr, "diffs": td,
                        "spike": spike, "tail10": tail10})
        print(f"s{seed}: 帧数={len(fr)} 尖峰={spike:.2f} 尾10均={tail10:.2f}", flush=True)
    if not results:
        sys.exit("[FAIL] 所有 seed 均失败")
    results.sort(key=lambda r: r["spike"])
    best = results[0]
    print(f"\n选中: s{best['seed']}（尖峰 {best['spike']:.2f} 最小）", flush=True)
    return best


def concat(segments, out_path):
    """ffmpeg 无损拼接（-c copy，不重编码，保持 20fps 原生流）。"""
    tmp = os.path.join(os.path.dirname(out_path), "_concat_tmp")
    os.makedirs(tmp, exist_ok=True)
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for s in segments:
            f.write(f"file '{os.path.basename(s)}'\n")
    for s in segments:
        shutil.copy(s, os.path.join(tmp, os.path.basename(s)))
    subprocess.run([config.ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat",
                    "-safe", "0", "-i", lst, "-c", "copy", out_path], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return out_path


def qa_master(fi, ft, fg, out_dir, run):
    """交付级 QA：总览条 + 接缝对比。"""
    def strip(fr, name):
        n = len(fr)
        idx = [0, n // 4, n // 2, 3 * n // 4, n - 1]
        row = np.hstack([cv2.resize(fr[i], (210, 382)) for i in idx])
        cv2.putText(row, name, (6, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        return row
    api.imwrite_u(os.path.join(out_dir, "QA_master.png"),
                  np.vstack([strip(fi, "idle1"), strip(ft, "transition"), strip(fg, "greet")]))
    seam = np.hstack([cv2.resize(x, (210, 382)) for x in [fi[-1], ft[0], ft[-1], fg[0]]])
    for j, lab in enumerate(["idle1 last", "trans first", "trans last", "greet first"]):
        cv2.putText(seam, lab, (j * 210 + 6, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    api.imwrite_u(os.path.join(out_dir, "QA_seams.png"), seam)


def parse_args():
    ap = argparse.ArgumentParser(description="v28 三段链条：idle → LTX 过渡 → greet")
    ap.add_argument("--run", default="v28", help="运行名（输出前缀 + runs/<名>/）")
    ap.add_argument("--idle-pose", default="assets/pose/idle1.mp4",
                    help="idle 姿态视频（本地文件或 ComfyUI input 里的文件名）")
    ap.add_argument("--greet-pose", default="assets/pose/greet_handtrim.mp4",
                    help="greet 姿态视频")
    ap.add_argument("--ref-image", default=None,
                    help="SCAIL 参考图（本地文件或 input 里的文件名）；默认用底快照里的 5186 图")
    ap.add_argument("--scail-recipe", default="scail_antijitter_v28.json",
                    help="SCAIL 提示词配方文件名（recipes/ 下）")
    ap.add_argument("--idle", default=None, help="直接用已生成的 idle 成片段（跳过 SCAIL idle）")
    ap.add_argument("--greet", default=None, help="直接用已生成的 greet 成片段（跳过 SCAIL greet）")
    ap.add_argument("--cap", type=int, default=None, help="帧数上限（默认=姿态视频实际帧数）")
    ap.add_argument("--frames", type=int, default=None, help="过渡帧数（默认取配方，须 1+8n）")
    ap.add_argument("--guide", type=float, default=None, help="双端 guide 强度（默认取配方 0.85）")
    ap.add_argument("--seeds", type=int, nargs="+", default=None, help="过渡 seed 列表（默认取配方）")
    ap.add_argument("--out", default=config.DESKTOP_DELIVERY, help="交付目录")
    ap.add_argument("--old-greet", default=None,
                    help="旧 greet 成片路径；给了则打印抖动对比（诊断用）")
    return ap.parse_args()


def main():
    args = parse_args()
    ltx, sc_recipe = load_recipes(args.scail_recipe)
    frames = args.frames or ltx["frames"]
    guide = args.guide if args.guide is not None else ltx["guide"]
    seeds = args.seeds or ltx["seeds"]
    run_dir = os.path.join(config.RUNS_DIR, args.run)
    os.makedirs(run_dir, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)

    # ---------- 1. SCAIL 动作迁移 ----------
    if args.greet:
        greet_mp4 = args.greet
        fn = api.frames_of(greet_mp4)
        print(f"跳过 SCAIL greet，使用 {greet_mp4}（{len(fn)} 帧）", flush=True)
    else:
        greet_mp4, fn = run_scail("greet", args.greet_pose, args.run, args.cap, sc_recipe,
                                  ref_image=args.ref_image)
        if args.old_greet:
            of = api.frames_of(args.old_greet)
            oj, nj = api.jitter_index(of), api.jitter_index(fn)
            print(f"\n抖动对比: 旧 jitter={oj:.3f} -> 新 jitter={nj:.3f} "
                  f"({'改善' if nj < oj else '变差'} {abs(nj - oj) / max(oj, 1e-6):.0%})", flush=True)

    if args.idle:
        idle_mp4 = args.idle
        fi = api.frames_of(idle_mp4)
        print(f"跳过 SCAIL idle，使用 {idle_mp4}（{len(fi)} 帧）", flush=True)
    elif args.idle_pose:
        idle_mp4, fi = run_scail("idle", args.idle_pose, args.run, args.cap, sc_recipe,
                                 ref_image=args.ref_image)
    else:
        sys.exit("[FAIL] 需要 --idle-pose 或 --idle 之一")

    # ---------- 2. 端点帧 ----------
    ep_idle = os.path.join(run_dir, "ep_idle_last.png")
    ep_greet = os.path.join(run_dir, "ep_greet_first.png")
    api.imwrite_u(ep_idle, fi[-1])
    api.imwrite_u(ep_greet, fn[0])
    n1, n2 = api.upload_image(ep_idle), api.upload_image(ep_greet)
    print(f"\n端点帧: idle末={n1} greet首={n2}", flush=True)

    # ---------- 3. LTX 过渡（多 seed 选优）----------
    g = api.gray
    gd = api.frame_diffs(fn)
    greet5 = float(np.mean(gd[:5]))
    best = run_ltx_transition(n1, n2, args.run, ltx, seeds, frames, guide)
    # 接缝色彩匹配：Q4 量化 LTX 比 SCAIL 段暗约 4 色阶，自校准 offset 补偿（配方开关）
    if ltx.get("color_match"):
        ft_c, cm_path = api.color_match_transition(fi, best["frames"], fn, run_dir, tag=args.run)
        if cm_path:
            best["src"] = cm_path
            print(f"色彩匹配完成: {cm_path}", flush=True)
        best["frames"] = ft_c
    ft, td = best["frames"], api.frame_diffs(best["frames"])
    best["diffs"] = td
    best["spike"] = max(td) if td else best["spike"]
    chest = api.roi_mean_diff(ft, 700, 1020, 190, 500)
    sb = float(np.abs(g(ft[-1]) - g(fn[0])).mean())
    thl = len(td) // 3
    print(f"过渡: 三段={np.mean(td[:thl]):.2f}/{np.mean(td[thl:2*thl]):.2f}/{np.mean(td[2*thl:]):.2f} "
          f"胸={chest:.2f} 尖峰={best['spike']:.2f} 尾10/greet5={best['tail10']/max(greet5,1e-6):.2f} "
          f"接缝B差={sb:.2f}", flush=True)

    # ---------- 4. 拼接 + 交付 ----------
    concat_path = os.path.join(args.out, f"完整衔接_{args.run}.mp4")
    concat([idle_mp4, best["src"], greet_mp4], concat_path)
    shutil.copy(greet_mp4, os.path.join(args.out, f"{args.run}_3_greet.mp4"))
    shutil.copy(best["src"], os.path.join(args.out, f"{args.run}_2_过渡.mp4"))
    shutil.copy(idle_mp4, os.path.join(args.out, f"{args.run}_1_idle.mp4"))

    fc = api.frames_of(concat_path)
    dc = api.frame_diffs(fc)
    b1, b2 = len(fi), len(fi) + len(ft)
    tdf = dc[b1:b2 - 1]
    thl = len(tdf) // 3
    print(f"\n===== {args.run} 交付核验 =====")
    print(f"拼接 {len(fc)}帧 {len(fc)/config.FPS:.2f}s | idle {len(fi)}帧 + 过渡 {len(ft)}帧 + greet {len(fn)}帧")
    print(f"过渡: 三段={np.mean(tdf[:thl]):.2f}/{np.mean(tdf[thl:2*thl]):.2f}/{np.mean(tdf[2*thl:]):.2f} "
          f"尖峰={max(tdf):.2f}")
    print(f"接缝 A={dc[b1-1]:.2f} B={dc[b2-1]:.2f}")
    qa_master(fi, ft, fn, args.out, args.run)
    print("交付:", sorted(f for f in os.listdir(args.out) if not f.startswith("QA_")))


if __name__ == "__main__":
    main()
