# -*- coding: utf-8 -*-
"""提交/回收 LTX 过渡生成任务（与 regen_transitions.py 分开的轻量版）。

regen_transitions.py 的等待超时是 30 分钟，而带 STG+cfg 的单段要 35~40 分钟，
会被跳过。本脚本只做两件事、都不长等待：
  submit：把剩余配对一次性提交进 ComfyUI 队列（顺序执行），映射写到 runs/<run>/map.json
  collect：按 map.json 等产出文件落盘，复制成段文件，打印尖峰

用法（引擎 venv，项目根 digital-human/）：
    python studio/transition_queue.py submit --pairs "idle2 to idle3" ... --run guizi_t3
    python studio/transition_queue.py collect --run guizi_t3
"""
import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
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
SEG_DIR = ENGINE / "guizi-character"


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


def prefix_of(run, a, b, seed):
    return f"gzt_{run}_{a}2{b}_s{seed}"


def cmd_submit(args):
    run_dir = ROOT / "runs" / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    recipe = json.loads((ROOT / "generation" / "recipes" / args.recipe).read_text(encoding="utf-8"))
    seed = args.seed
    mapping = {}
    # 端点帧缓存
    ep = {}
    # 2026-10-29 重大修复：这里曾用模块常量 SEG_DIR（鬼子库）而无视 --seg-dir，
    # 导致所有新数字人的过渡端点帧取自鬼子段——LTX 在鬼子帧之间插值，过渡段
    # 全部变成鬼子素材（webtest003/004/005 三条产线全中招）
    seg_dir = Path(args.seg_dir)
    pairs = [tuple(o.split(" to ")) for o in args.pairs]
    for a, b in pairs:
        for seg in (a, b):
            if (seg, "first") not in ep:
                fr = frames_of(seg_dir / f"{seg}.mp4")
                ep[(seg, "first")] = fr[0]
                ep[(seg, "last")] = fr[-1]
    for a, b in pairs:
        ea = run_dir / f"ep_{a}_last.png"
        eb = run_dir / f"ep_{b}_first.png"
        api.imwrite_u(ea, ep[(a, "last")])
        api.imwrite_u(eb, ep[(b, "first")])
        na, nb = api.upload_image(ea), api.upload_image(eb)
        pfx = prefix_of(args.run, a, b, seed)
        n_frames = args.frames or recipe["frames"]
        graph = ltx_flf.build(na, nb, pfx, pos=recipe["pos"], neg=recipe["neg"],
                              nag=recipe["neg_nag"], frames=n_frames, seed=seed,
                              guide=recipe["guide"], fps=args.fps, width=W, height=H,
                              stg_scale=recipe["stg_scale"], stg_blocks=recipe["stg_blocks"],
                              cfg=1.0 if args.fast else recipe["cfg"])
        if args.fast:
            # 官方 FLF2V 风格：CFG=1、去掉 STG(14)/NAG(11)，模型直连 GGUF。
            # 2026-11-05 实测：旧 cfg2.5+STG+NAG 在 81 帧下中段爆 21 帧全黑，
            # 且单条 30+ 分钟；官方风格 1.5 分钟、0 黑帧、尖峰 0.96。
            graph.pop("14", None)
            graph.pop("11", None)
            graph["43"]["inputs"]["model"] = ["10", 0]
            graph["43"]["inputs"]["cfg"] = 1.0
        # 分模块：每对过渡工作流 JSON 落盘（绑定新数字人自己的端点帧）
        wf_dir = run_dir / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        (wf_dir / f"ltx_{a.replace(' ', '')}2{b.replace(' ', '')}_s{seed}.json").write_text(
            json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        pid = api.submit(graph)
        print(f"[{a}→{b}] {pfx} -> {pid}", flush=True)
        mapping[f"{a} to {b}"] = {"prefix": pfx, "pid": pid, "ts": time.time()}
        time.sleep(1)
    (run_dir / "map.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已提交 {len(mapping)} 段，映射: {run_dir / 'map.json'}", flush=True)


def check_pid(pid: str, started: dict, stall_minutes: int = 40):
    """⑥ ComfyUI 看门狗：对单个 pending pid 做健康检查。

    返回 None = 仍在正常执行（排队/运行/已完成待落盘）；返回字符串 = 失败原因：
    - GET /history/{pid} status_str=error → 执行报错；
    - GET /queue 确认 pid ∈ queue_running 且持续超 stall_minutes 分钟 →
      显存不足动态换载的死锁形态，POST /interrupt 杀掉并记失败，
      交产线自愈重掷（限定"确认在 running"才动手，避免误伤排队/他人任务）。
    """
    try:
        h = json.load(urllib.request.urlopen(
            config.SERVER + "/history/" + pid, timeout=30))
    except Exception:
        return None  # history 查不到（仍在执行）不武断判死
    entry = h.get(pid)
    if entry and entry.get("status", {}).get("status_str") == "error":
        return "ComfyUI 执行 error"
    try:
        q = json.load(urllib.request.urlopen(config.SERVER + "/queue", timeout=30))
    except Exception:
        return None
    running = {it[1] for it in q.get("queue_running", [])
               if isinstance(it, (list, tuple)) and len(it) > 1}
    if pid not in running:
        started.pop(pid, None)  # 还在排队/已切换：重置看门狗计时
        return None
    t0 = started.setdefault(pid, time.time())
    if time.time() - t0 > stall_minutes * 60:
        try:
            urllib.request.urlopen(urllib.request.Request(
                config.SERVER + "/interrupt", data=b"", method="POST"), timeout=30)
        except Exception:
            pass
        return f"运行超 {stall_minutes} 分钟疑似死锁，已 POST /interrupt"
    return None


def cmd_collect(args):
    run_dir = ROOT / "runs" / args.run
    map_path = run_dir / "map.json"
    if not map_path.is_file():
        sys.exit(f"map.json 不存在: {map_path}")  # 提交记录缺失仍判死（⑤）
    seg_dir = Path(args.seg_dir)
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    pending = dict(mapping)
    started = {}   # pid -> 首次观察到 running 的时刻（看门狗计时基准）
    failed = {}    # pair -> 失败原因（error / 看门狗 interrupt）
    deadline = time.time() + args.timeout
    while pending and time.time() < deadline:
        for name, info in list(pending.items()):
            # 只认本任务 history 报出的产出；绝不按固定 _00001_ 判断（旧文件
            # 会让 collect 立即"完成"并复制上一版过渡，2026-11-05 修复）。
            src = api.resolve_job_output(
                info["pid"], info.get("prefix", ""), info.get("ts", 0))
            if src and os.path.exists(src) and os.path.getsize(src) > 10000:
                fr = frames_of(src)
                if len(fr) >= 20:
                    means = [float(f.mean()) for f in fr]
                    black = sum(1 for m in means if m < 8)
                    head = float(np.mean(means[:3]))
                    tail = float(np.mean(means[-3:]))
                    # 2026-11-05 实测：cfg2.5+STG+NAG 在 81 帧会中段爆 21 帧全黑，
                    # 旧逻辑只警告不拦截 → 黑帧直接进段库。这里硬拦，交自愈重掷。
                    if black or min(means) < max(8.0, 0.35 * min(head, tail)):
                        failed[name] = (f"亮度异常（黑帧 {black}/{len(fr)}，"
                                        f"min={min(means):.1f} head={head:.1f} tail={tail:.1f}）")
                        del pending[name]
                        print(f"[{name}] 失败回收：{failed[name]}", flush=True)
                        continue
                    spike = max(cv2.absdiff(fr[i], fr[i + 1]).mean() for i in range(len(fr) - 1))
                    warn = "" if abs(head - tail) < 8 else f" ⚠首尾亮度差 {head:.1f}/{tail:.1f}"
                    dst = seg_dir / f"{name}.mp4"
                    shutil.copy2(src, dst)
                    print(f"[{name}] {len(fr)}帧 尖峰={spike:.2f}{warn} -> {dst.name}", flush=True)
                    del pending[name]
                    continue
            reason = check_pid(info["pid"], started)
            if reason:
                failed[name] = reason
                del pending[name]
                print(f"[{name}] 失败回收：{reason}", flush=True)
        if pending:
            time.sleep(300)
    # ⑤ 软失败：未完成（collect 超时或看门狗判死）写 missing.json 后正常退出，
    # 产线（make_avatar 过渡自愈循环）读它重掷——不再一票否决整条任务
    unfinished = {**failed, **{k: "collect 超时" for k in pending}}
    if unfinished:
        missing = [{"pair": k, "prefix": mapping[k]["prefix"],
                    "pid": mapping[k]["pid"], "reason": v}
                   for k, v in sorted(unfinished.items())]
        (run_dir / "missing.json").write_text(
            json.dumps(missing, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"未完成 {len(missing)} 项已写入 {run_dir / 'missing.json'}"
              "（软失败，交产线自愈）", flush=True)
        return
    print("全部完成", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["submit", "collect"])
    ap.add_argument("--pairs", nargs="+", default=[])
    ap.add_argument("--run", required=True)
    ap.add_argument("--recipe", default="ltx_transition_guizi.json")
    ap.add_argument("--seed", type=int, default=801)
    ap.add_argument("--frames", type=int, default=0,
                    help="覆盖配方的帧数（0=用配方值）。差配对（guide 对像素差 >25）"
                         "用 81 帧重熔：同样的形变量摊到更多帧，单帧尖峰降 ~30%"
                         "（v24→v28 的实证结论：加长过渡段缓解尾部收敛抖动）")
    ap.add_argument("--fps", type=int, default=25,
                    help="生成帧率（LTX 官方 FLF2V 用 25fps；动作段 RIFE 后也是 25fps，"
                         "避免 build_avatar 再补帧）")
    ap.add_argument("--fast", action="store_true",
                    help="官方 FLF2V 风格 CFG=1、去 STG/NAG（稳定且快 20 倍）")
    ap.add_argument("--timeout", type=int, default=8 * 3600)
    ap.add_argument("--seg-dir", default=str(SEG_DIR),
                    help="collect 时过渡段文件的落地目录（默认鬼子目录；"
                         "make_avatar 产线对其他人物必须传各自的 staging 目录）")
    args = ap.parse_args()
    if args.cmd == "submit":
        if not args.pairs:
            sys.exit("submit 需要 --pairs")
        cmd_submit(args)
    else:
        cmd_collect(args)


if __name__ == "__main__":
    main()
