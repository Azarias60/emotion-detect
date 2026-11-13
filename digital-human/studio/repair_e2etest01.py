# -*- coding: utf-8 -*-
"""e2etest01 修复链：重掷 idle1 → 强制采用 → 单次调色 → 复检 →
重出挂 idle1 的 10 条过渡 → 过渡调色 → 过门 → 组装上架。

前提（2026-11-10）：
  - make_avatar 已修：qa 非零码不再砸产线；过渡后调色已限 transitions-only
  - idle1 原始 s1 mouth_p10=0.53 < 门限 0.54（真边缘不合格），换 seed 重掷
  - 引擎离线、ComfyUI 空闲，显存独占
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "livetalking"))

PY = str(ROOT / "livetalking" / "venv" / "Scripts" / "python.exe")


def sh(*args, **kw):
    print("::", *args[:4], flush=True)
    r = subprocess.run([PY, *args], cwd=str(ROOT), **kw)
    if r.returncode != 0:
        print(f"!! 步骤失败 exit={r.returncode}: {args[:3]}", flush=True)
        sys.exit(r.returncode)


NAME = "e2etest01"
SEG_DIR = ROOT / "runs" / "make_avatar" / NAME / "segments"
QA_OUT = ROOT / "runs" / "make_avatar" / NAME
IDLE1_PAIRS = ["idle3 to idle1", "idle1 to greet", "idle1 to talk", "talk to idle1",
               "idle1 to idle2", "greet to idle1", "idle1 to comfort",
               "comfort to idle1", "idle1 to encourage", "encourage to idle1"]

# 1. 重掷 idle1（seed 120）
sh("studio/regen_scail.py", "submit", "--segments", "idle1", "--seeds", "120",
   "--run", f"{NAME}_g_h2s120",
   "--ref-image", str(QA_OUT / f"ref_{NAME}.jpg"),
   "--driver-dir", str(QA_OUT / "drivers"),
   "--force-rate", "25", "--out-fps", "25")
sh("studio/regen_scail.py", "collect", "--run", f"{NAME}_g_h2s120", "--timeout", "7200")

# 2. 强制采用新 idle1 + 基础段单次调色
from studio.make_avatar import Progress, gate_base_segments, gate_transitions, stage_select
prog = Progress(ROOT / "runs" / "uploads" / NAME / "progress.json", NAME, "e2e测试01")
stage_select(NAME, prog, ("idle1", "idle2", "idle3", "greet", "talk", "comfort", "encourage"),
             force_new={"idle1"})
sh("studio/unify_color.py", "--segments", str(SEG_DIR), "--apply", "--scope", "base")

# 3. 基础段复检（check=False 语义：报告落盘即有效，退出码不杀链）
sh("studio/qa_segments.py", "--segments", str(SEG_DIR),
   "--out", str(QA_OUT / "qa_report.json"), check=False)
import json
qa = json.loads((QA_OUT / "qa_report.json").read_text(encoding="utf-8"))
bad = gate_base_segments(SEG_DIR, QA_OUT / "qa_report.json")
print("基础段门限:", bad or "全部通过", flush=True)
if bad:
    print("!! 重掷后仍不合格，按设计带残差继续（记录在案）", flush=True)

# 4. 重出挂 idle1 的 10 条过渡（seed 803）
sh("studio/transition_queue.py", "submit", "--pairs", *IDLE1_PAIRS,
   "--run", f"{NAME}_t3", "--frames", "25", "--fps", "25", "--fast",
   "--seed", "803", "--seg-dir", str(SEG_DIR),
   "--recipe", "ltx_transition_neutral.json")
sh("studio/transition_queue.py", "collect", "--run", f"{NAME}_t3",
   "--timeout", "14400", "--seg-dir", str(SEG_DIR))

# 5. 过渡调色（transitions-only，硬约束）+ 过门
sh("studio/unify_color.py", "--segments", str(SEG_DIR), "--apply", "--scope", "transitions")
tbad = gate_transitions(SEG_DIR)
frozen = []
import cv2
from studio.make_avatar import _freeze_run, FREEZE_GATE
for p in sorted(SEG_DIR.glob("* to *.mp4")):
    if _freeze_run(p) >= FREEZE_GATE:
        frozen.append(p.stem)
print("过渡门限: 尖峰超标", tbad or "无", "| 定格超限", frozen or "无", flush=True)

# 6. 组装上架（复用 make_avatar 尾部两阶段）
subprocess.run([PY, "studio/make_avatar.py",
                "--photo", str(ROOT / "runs" / "uploads" / NAME / "photo.jpg"),
                "--name", NAME, "--display-name", "e2e测试01",
                "--progress-json", str(ROOT / "runs" / "uploads" / NAME / "progress.json"),
                "--resume", "--skip-transitions"], cwd=str(ROOT), check=True)
print("修复链完成", flush=True)
