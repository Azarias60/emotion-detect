#!/bin/bash
# 启动 ComfyUI 并提交 14 个 int8 任务（在 deploy/comfyui-int8/ 下跑：bash run_all.sh）
set -e
cd "$(dirname "$0")"
PORT="${1:-8188}"

if [ ! -d venv ]; then
  echo "未安装依赖，先跑: bash install_linux.sh"; exit 1
fi

echo "==> 启动 ComfyUI (端口 $PORT)"
./venv/bin/python ComfyUI/main.py --listen 0.0.0.0 --port "$PORT" > comfyui.log 2>&1 &
COMFY_PID=$!
trap "kill $COMFY_PID 2>/dev/null" EXIT

echo "==> 等待服务就绪…"
for i in $(seq 1 60); do
  if curl -s "http://127.0.0.1:$PORT/system_stats" > /dev/null 2>&1; then break; fi
  sleep 2
done
curl -s "http://127.0.0.1:$PORT/system_stats" > /dev/null || { echo "ComfyUI 启动失败，看 comfyui.log"; exit 1; }
echo "==> 已就绪"

echo "==> 提交 14 个任务（7 段 × seeds 1,2）"
./venv/bin/python jobs/submit_all.py "http://127.0.0.1:$PORT"

echo ""
echo "==> 产出在 ComfyUI/output/scail2/，拷回本机即可接产线。"
