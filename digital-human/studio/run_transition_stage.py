# -*- coding: utf-8 -*-
"""跑 make_avatar.py 的**过渡阶段**（原样复用产线代码），只生成指定过渡对。

不重跑 SCAIL 基础段（那要 1~2 小时）——基础段已在
runs/make_avatar/<name>/segments/ 就位，过渡阶段只做：
    submit（产线同参：--frames 25 --fps 25 --fast --seed 801 --recipe neutral）
    → collect（含黑帧硬拦）
    → gate_transitions + 定格门（产线同函数）
    → 不过门换 seed 802/803 重掷（产线同循环）
    → 全过：unify_color --scope transitions（产线同参）
    → build_avatar 重组装

用法：
    python studio/run_transition_stage.py --name e2etest01 --pairs "idle3 to idle2" "idle2 to idle1"
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "studio"
sys.path.insert(0, str(STUDIO))
sys.path.insert(0, str(ROOT / "livetalking"))
sys.path.insert(0, str(ROOT / "generation"))

import make_avatar as ma  # noqa: E402  产线本体：gate_transitions / _freeze_run / FREEZE_GATE


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.stdout:
        print(r.stdout[-2000:], flush=True)
    if r.returncode != 0:
        print(r.stderr[-3000:], flush=True)
        raise SystemExit(f"命令失败: {' '.join(cmd)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="e2etest01")
    ap.add_argument("--pairs", nargs="+", required=True)
    args = ap.parse_args()

    name = args.name
    stage_dir = ROOT / "runs" / "make_avatar" / name / "segments"
    if not stage_dir.is_dir():
        raise SystemExit(f"段目录不存在: {stage_dir}（基础段未就位，不能只跑过渡）")

    pairs = list(args.pairs)
    display = json.loads((ROOT / "livetalking" / "data" / "presets.json").read_text(encoding="utf-8"))
    display_name = next((p.get("name", name) for p in display if p.get("id") == name), name)

    t_run = f"{name}_t"
    todo = list(pairs)
    remaining: list[str] = []

    # ── 产线过渡自愈循环（≤3 轮：seed 801/802/803）────────────────────────
    for rnd, seed in enumerate((801, 802, 803), start=1):
        if not todo:
            break
        run_id = t_run if rnd == 1 else f"{t_run}r{rnd}"
        run([sys.executable, "studio/transition_queue.py", "submit", "--pairs", *todo,
             "--run", run_id, "--frames", "25", "--fps", "25", "--fast",
             "--seed", str(seed), "--seg-dir", str(stage_dir),
             "--recipe", "ltx_transition_neutral.json"])
        run([sys.executable, "studio/transition_queue.py", "collect", "--run", run_id,
             "--timeout", "10800", "--seg-dir", str(stage_dir)])
        # 终检门：尖峰>10 ∪ 定格超限 ∪ collect 软失败（missing.json）
        rest = set(ma.gate_transitions(stage_dir))
        for p in sorted(stage_dir.glob("* to *.mp4")):
            if ma._freeze_run(p) >= ma.FREEZE_GATE:
                rest.add(p.stem)
        miss_path = ROOT / "runs" / run_id / "missing.json"
        if miss_path.is_file():
            rest |= {m["pair"] for m in json.loads(miss_path.read_text(encoding="utf-8"))
                     if not (stage_dir / f"{m['pair']}.mp4").is_file()}
        rest &= set(pairs)
        if not rest:
            remaining = []
            break
        remaining = sorted(rest)
        if rnd < 3:
            print(f"过渡第 {rnd} 轮后仍差 {remaining}，换 seed {seed + 1} 重掷", flush=True)
            todo = remaining
    if remaining:
        print(f"3 轮自愈后仍差: {remaining}（带残差继续）", flush=True)
    elif pairs:
        run([sys.executable, "studio/unify_color.py", "--segments", str(stage_dir),
             "--apply", "--scope", "transitions"])

    # ── 重组装（产线同参）───────────────────────────────────────────────
    run([sys.executable, "studio/build_avatar.py", "--segments", str(stage_dir),
         "--avatar-id", name, "--display-name", display_name])
    print(f"\n过渡阶段完成：{name}（本次 {len(pairs)} 对）")


if __name__ == "__main__":
    main()
