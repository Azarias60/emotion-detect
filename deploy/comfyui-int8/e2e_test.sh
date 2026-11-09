#!/bin/bash
# ============================================================
# comfyui-int8 Linux 端到端测试（分阶段，冒烟不过就停）
#
# 用法:
#   bash e2e_test.sh           # 从阶段 1 顺序跑（每阶段前需按 y 确认）
#   bash e2e_test.sh 3         # 只跑阶段 3（环境/依赖已就绪时）
#
# 阶段:
#   1 环境自检    GPU/python/磁盘/git
#   2 装依赖      install_linux.sh（torch + clone 5 插件）
#   3 冒烟        起 ComfyUI → SCAIL 单条 idle1_s1 → 验产出
#   4 SCAIL 批量  14 条（可 --no-wait 后台跑）
#   5 LTX 冒烟    submit_ltx.py --smoke（2.5 图首次验证）
#   6 LTX 批量    14 条过渡
#   7 产出验收    文件数/帧数/分辨率对账 + 生成拉回清单
# ============================================================
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8188}"
PY="./venv/bin/python"

hr() { echo "------------------------------------------------------------"; }

ask() {
  read -p ">>> 进入阶段 $1：$2  [y/N] " a
  [ "$a" = "y" ] || [ "$a" = "Y" ]
}

# ---------- 阶段 1：环境自检 ----------
stage1() {
  hr; echo "[阶段1] 环境自检"; hr
  echo "--- GPU:"; nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv || {
    echo "✗ nvidia-smi 失败——没有 NVIDIA 驱动/GPU，后续都跑不了"; exit 1; }
  echo "--- Python:"; python3 --version || { echo "✗ 缺 python3.10+"; exit 1; }
  echo "--- git:"; git --version || { echo "✗ 缺 git（clone 插件要用）"; exit 1; }
  echo "--- 磁盘（需要: 模型58G + torch5G + 产出~5G ≈ 70G 空闲）:"
  df -h . | tail -1
  echo "--- 模型（58G 传完了吗）:"
  du -sh models 2>/dev/null || echo "  models/ 不存在（传 models_int8 到位后再跑阶段3+）"
  echo "✓ 阶段1 自检完"
}

# ---------- 阶段 2：装依赖 ----------
stage2() {
  hr; echo "[阶段2] 装依赖（torch ~2.5G 下载 + clone 插件）"; hr
  bash install_linux.sh
  echo "✓ 阶段2 完成：$PY 与 5 个插件就绪"
}

# ---------- 起 ComfyUI ----------
start_comfy() {
  if curl -s "http://127.0.0.1:$PORT/system_stats" > /dev/null 2>&1; then
    echo "ComfyUI 已在端口 $PORT 运行"; return
  fi
  echo "启动 ComfyUI（端口 $PORT，日志 comfyui.log）…"
  $PY ComfyUI/main.py --listen 0.0.0.0 --port "$PORT" > comfyui.log 2>&1 &
  for i in $(seq 1 90); do
    curl -s "http://127.0.0.1:$PORT/system_stats" > /dev/null 2>&1 && break
    sleep 2
  done
  curl -s "http://127.0.0.1:$PORT/system_stats" > /dev/null || {
    echo "✗ ComfyUI 90 秒未就绪，看 comfyui.log 尾部:"; tail -20 comfyui.log; exit 1; }
  echo "ComfyUI 就绪"
}

# ---------- 阶段 3：SCAIL 单条冒烟 ----------
stage3() {
  hr; echo "[阶段3] SCAIL int8 冒烟（单条 idle1_s1）"; hr
  start_comfy
  $PY jobs_scail/submit_scail.py "http://127.0.0.1:$PORT" --only idle1_s1
  # 验产出: 94 帧 704x1280（与素材帧数一致）
  OUT=$(find ComfyUI/output -name "e2egreet01_g_idle1_s1_*.mp4" | head -1)
  [ -n "$OUT" ] || { echo "✗ 没找到产出文件"; exit 1; }
  $PY - "$OUT" <<'PY'
import sys, cv2
p = sys.argv[1]
cap = cv2.VideoCapture(p)
n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
cap.release()
print(f"产出: {n} 帧 {w}x{h}")
ok = n >= 90 and w == 704 and h == 1280
print("✓ 帧数/分辨率符合预期" if ok else "✗ 帧数或分辨率不对（应 ~94 帧 704x1280）")
sys.exit(0 if ok else 1)
PY
  echo "✓ 阶段3 冒烟通过——int8 主模型+umt5+SAM 链路 OK"
}

# ---------- 阶段 4：SCAIL 批量 ----------
stage4() {
  hr; echo "[阶段4] SCAIL int8 批量 14 条"; hr
  start_comfy
  $PY jobs_scail/submit_scail.py "http://127.0.0.1:$PORT" --no-wait
  echo "已后台提交。盯进度: tail -f comfyui.log"
  echo "全部完成后跑阶段 5/7。"
}

# ---------- 阶段 5：LTX 冒烟 ----------
stage5() {
  hr; echo "[阶段5] LTX 2.5 int8 冒烟（单条过渡）"; hr
  start_comfy
  $PY jobs_ltx/submit_ltx.py "http://127.0.0.1:$PORT" --smoke
  echo "✓ 阶段5 冒烟通过——2.5 图兼容性 OK"
}

# ---------- 阶段 6：LTX 批量 ----------
stage6() {
  hr; echo "[阶段6] LTX 2.5 int8 批量 14 条"; hr
  start_comfy
  $PY jobs_ltx/submit_ltx.py "http://127.0.0.1:$PORT"
  echo "✓ 阶段6 完成"
}

# ---------- 阶段 7：产出验收 ----------
stage7() {
  hr; echo "[阶段7] 产出验收 + 拉回清单"; hr
  $PY - <<'PY'
import os, cv2
EXPECT_SCAIL = [("idle1", 94), ("idle2", 113), ("idle3", 77), ("greet", 100),
                ("talk", 100), ("comfort", 110), ("encourage", 104)]
base = "ComfyUI/output/scail2"
rows, bad = [], []
for seg, exp in EXPECT_SCAIL:
    for seed in (1, 2):
        f = os.path.join(base, f"e2egreet01_g_{seg}_s{seed}_00001_.mp4")
        if not os.path.isfile(f):
            rows.append(f"  ✗ {seg}_s{seed} 缺失"); bad.append(f"{seg}_s{seed}"); continue
        cap = cv2.VideoCapture(f)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); cap.release()
        ok = abs(n - exp) <= 2
        if not ok: bad.append(f"{seg}_s{seed}({n}≠{exp})")
        rows.append(f"  {'✓' if ok else '✗'} {seg}_s{seed}: {n} 帧（素材 {exp}）")
trans = [f for f in os.listdir("ComfyUI/output") if f.startswith("gzt_e2egreet01_t_") and f.endswith(".mp4")]
rows.append(f"  过渡产出: {len(trans)}/14 个 gzt_*.mp4")
if len(trans) < 14: bad.append(f"过渡缺 {14-len(trans)}")
print("\n".join(rows))
print("\n验收: " + ("✓ 全部通过" if not bad else f"✗ 问题: {bad}"))
# 拉回清单（拷回本机用）
os.makedirs("pull_back", exist_ok=True)
with open("pull_back/FILES_TO_PULL.txt", "w") as fh:
    for seg, _ in EXPECT_SCAIL:
        for seed in (1, 2):
            fh.write(f"ComfyUI/output/scail2/e2egreet01_g_{seg}_s{seed}_00001_.mp4\n")
    for t in sorted(trans):
        fh.write(f"ComfyUI/output/{t}\n")
print("拉回清单已写: pull_back/FILES_TO_PULL.txt")
PY
}

# ---------- 主流程 ----------
FROM="${1:-1}"
for s in $(seq "$FROM" 7); do
  case $s in
    1) ask 1 "环境自检（GPU/python/磁盘/模型）" && stage1 ;;
    2) ask 2 "装依赖（torch+插件，约10分钟）" && stage2 ;;
    3) ask 3 "SCAIL int8 单条冒烟（5~25分钟）" && stage3 ;;
    4) ask 4 "SCAIL int8 批量14条（1~2小时）" && stage4 ;;
    5) ask 5 "LTX 2.5 int8 冒烟（5~15分钟）" && stage5 ;;
    6) ask 6 "LTX 2.5 int8 批量14条" && stage6 ;;
    7) ask 7 "产出验收+生成拉回清单" && stage7 ;;
  esac
done
echo ""
echo "端到端测试流程结束。产出按 pull_back/FILES_TO_PULL.txt 拉回本机，"
echo "然后本机跑: make_avatar --from-select（选优→后处理→上架）。"
