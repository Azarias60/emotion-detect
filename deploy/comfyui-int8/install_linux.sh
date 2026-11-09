#!/bin/bash
# Linux 一键装依赖（在 deploy/comfyui-int8/ 目录下跑：bash install_linux.sh）
# 需要: python3.10+、pip、git、约 5GB 磁盘装 torch(CUDA)
set -e
cd "$(dirname "$0")"
echo "==> 创建 venv"
python3 -m venv venv
./venv/bin/pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

echo "==> PyTorch (CUDA 12.4)"
./venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

echo "==> ComfyUI 主程序依赖"
./venv/bin/pip install -r ComfyUI/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo "==> git clone 5 个插件（custom_nodes 不在传输包内，远端自己下载）"
mkdir -p custom_nodes
clone() {
  local repo=$1 dir=$2
  if [ -d "$dir/.git" ]; then echo "  $dir 已存在，跳过"; return 0; fi
  echo "  clone $repo"
  git clone --depth 1 "https://github.com/$repo.git" "$dir" 2>/dev/null \
    || git clone --depth 1 "https://ghfast.top/https://github.com/$repo.git" "$dir" 2>/dev/null \
    || git clone --depth 1 "https://gitclone.com/github.com/$repo.git" "$dir" \
    || { echo "  ✗ $repo clone 失败，检查网络或手动 clone"; return 1; }
}
cd custom_nodes
clone city96/ComfyUI-GGUF            ComfyUI-GGUF
clone yolain/ComfyUI-Easy-Use        comfyui-easy-use
clone kijai/ComfyUI-KJNodes          comfyui-kjnodes
clone Kosinkadink/ComfyUI-VideoHelperSuite comfyui-videohelpersuite
clone ltdrdata/ComfyUI-Impact-Pack   comfyui-impact-pack
clone Fannovel16/ComfyUI-Frame-Interpolation ComfyUI-Frame-Interpolation
cd ..

echo "==> 插件依赖"
for p in ComfyUI-GGUF comfyui-easy-use comfyui-kjnodes comfyui-videohelpersuite comfyui-impact-pack ComfyUI-Frame-Interpolation; do
  if [ -f "custom_nodes/$p/requirements.txt" ]; then
    echo "  --- $p"
    ./venv/bin/pip install -r "custom_nodes/$p/requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple || echo "  ($p 部分依赖失败，继续)"
  fi
done

echo "==> RIFE 插帧模型（interpolate_rife.py 用，20MB，包内自带）"
mkdir -p custom_nodes/ComfyUI-Frame-Interpolation/ckpts/rife
if [ -f rife_ckpt/rife49.pth ]; then
  cp rife_ckpt/rife49.pth custom_nodes/ComfyUI-Frame-Interpolation/ckpts/rife/
  echo "  ✓ rife49.pth 已就位"
else
  echo "  ⚠ 包内缺 rife_ckpt/rife49.pth——从 Windows 本机拷"
  echo "    custom_nodes/ComfyUI-Frame-Interpolation/ckpts/rife/rife49.pth (20MB)"
fi

echo ""
echo "==> 检查模型文件（models/ 需单独放到本目录下）:"
python3 - <<'PY'
import os, sys
need = {
 "diffusion_models/wan2.1_14B_SCAIL_2_int8_convrot.safetensors": 15.0,
 "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors": 19.0,
 "text_encoders/umt5-xxl-encoder-Q4_K_M.gguf": 3.0,
 "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors": 13.0,
 "clip_vision/clip_vision_h.safetensors": 1.0,
 "checkpoints/sam3.1_multiplex_fp16.safetensors": 1.5,
 "loras/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors": 0.6,
 "vae/wan_2.1_vae.safetensors": 0.2,
 "vae/ltx-2.5-video-vae-conv-bf16.safetensors": 1.0,
 "vae/ltx-2.5-audio-vae-bf16.safetensors": 0.3,
}
missing = []
for f, min_gb in need.items():
    p = os.path.join("models", f)
    if not os.path.isfile(p):
        missing.append(f)
        continue
    gb = os.path.getsize(p) / 1073741824
    if gb < min_gb:
        print(f"  ✗ {f} 大小异常 ({gb:.2f} GB，应 ≥{min_gb}G，传输不完整？)")
        missing.append(f)
    else:
        print(f"  ✓ {f} ({gb:.2f} GB)")
if missing:
    print(f"\n  ⚠ {len(missing)}/{len(need)} 个模型缺失——models/ 是单独传的，")
    print("    把 models_int8/ 文件夹传到本目录下变成 models/ 再跑 run_all.sh。")
    print("    依赖与插件已装好，可以先做别的。")
else:
    print("\n  模型齐全。")
PY
echo ""
echo "依赖+插件就绪。模型齐了跑: bash run_all.sh"
