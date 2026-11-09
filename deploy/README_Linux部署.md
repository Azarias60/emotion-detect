# Linux 部署指南（ComfyUI int8 两套工作流）

数字人产线的 ComfyUI 生成部分已打包成 `deploy/comfyui-int8/`（约 90MB），
可在 Linux + NVIDIA GPU（4090 24G / A6000 / A100）上跑 **SCAIL2 int8** 基础段
和 **LTX 2.5 int8** 过渡。前后端/引擎仍在 Windows 跑。

## 1. 拿到代码

```bash
git clone <你的仓库地址>
cd <仓库目录>/deploy/comfyui-int8
```

## 2. 模型（不在 git 里，58GB，三种拿法任选）

| 文件 | 大小 | 拿法 |
|---|---|---|
| `diffusion_models/wan2.1_14B_SCAIL_2_int8_convrot.safetensors` | 15.5G | Windows 本机 `E:\Comfyui\...\models\diffusion_models\` 下同名文件（XFTP/scp 传） |
| `diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors` | 20.0G | 同上 |
| `text_encoders/umt5-xxl-encoder-Q4_K_M.gguf` | 3.4G | hf-mirror `QuantStack/umt5-xxl-encoder-GGUF` |
| `text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors` | 14.3G | Windows 本机 `models\text_encoders\` |
| `clip_vision/clip_vision_h.safetensors` | 1.2G | hf-mirror `Comfy-Org/Wan_2.1_ComfyUI_repackaged` 的 split_files/clip_vision |
| `checkpoints/sam3.1_multiplex_fp16.safetensors` | 1.6G | Windows 本机 `models\checkpoints\` |
| `loras/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors` | 0.7G | hf-mirror `Comfy-Org/Wan_2.1_ComfyUI_repackaged` 的 split_files/loras |
| `vae/wan_2.1_vae.safetensors` | 0.2G | 同上 split_files/vae |
| `vae/ltx-2.5-video-vae-conv-bf16.safetensors` | 1.4G | Windows 本机 `models\vae\` |
| `vae/ltx-2.5-audio-vae-bf16.safetensors` | 0.3G | Windows 本机 `models\vae\` |

放到 `comfyui-int8/models/` 对应子目录。

## 3. 装依赖 + 跑（端到端 7 阶段）

```bash
bash e2e_test.sh        # 从阶段1顺序跑，每阶段按 y 确认，冒烟不过自动停
bash e2e_test.sh 3      # 或单跑某阶段
```

| 阶段 | 内容 | 验收 |
|---|---|---|
| 1 环境自检 | GPU/python/磁盘/git | nvidia-smi 认出卡 |
| 2 装依赖 | torch CUDA + git clone 5 个插件（custom_nodes 不在包里） | install 退出码 0 |
| 3 SCAIL 冒烟 | 单条 idle1_s1 | 产出 ~94 帧 704×1280 |
| 4 SCAIL 批量 | 14 条（7 段×2 种子） | 1~2 小时 |
| 5 LTX 冒烟 | submit_ltx.py --smoke（2.5 图首验） | 出 25 帧过渡 |
| 6 LTX 批量 | 14 条 1 秒过渡 | — |
| 7 产出验收 | 帧数/分辨率对账 + 拉回清单 | 14+14 在位 |

## 4. 产出拉回 Windows 接产线

按 `pull_back/FILES_TO_PULL.txt` 把 28 个视频拷回 Windows：

```
E:\Comfyui\ComfyUI\No1基础包含文生图模型\ComfyUIportable\ComfyUIportable\ComfyUI\output\
```

然后本机跑：

```bash
cd E:\emotionDetect\digital-human
livetalking\venv\Scripts\python.exe studio\make_avatar.py \
    --photo runs/make_avatar/e2egreet01/ref_e2egreet01.jpg \
    --name e2egreet01 --display-name 量化版实测 \
    --progress-json runs/make_avatar/e2egreet01/progress.json --from-select
```

（选优→背景锁定→插帧→腿修→过渡→QA→融化帧修复→上架，全自动）

## 排障

见 `deploy/comfyui-int8/README.md` 排障表。
