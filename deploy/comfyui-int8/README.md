# comfyui-int8 部署包（XFTP 一拉就走）

## 这是什么

ComfyUI + 5 个插件 + 两套 int8 模型 + 28 个工作流 + 18 个后处理脚本，全部自包含。
传到 Linux 机器后两条命令：装依赖 → 提交任务。

| 套件 | 模型 | 用途 | 任务数 |
|---|---|---|---|
| **SCAIL2 int8** | wan2.1_14B_SCAIL_2_int8_convrot（15.5G） | 基础动作段生成 | 14（7 段 × 2 种子） |
| **LTX 2.5 int8** | ltx-2.5-22b-distilled-transformer-comfy-int8-convrot（20.0G） | 1 秒过渡生成 | 14（14 对动作） |

提示词为最新稳定版（按动作分 5 条 + 负向提示词）；过渡 25 帧 @25fps = 1 秒。

## 目录结构

```
comfyui-int8/                  （上传包，90 MB）
├── ComfyUI/            # 主程序（无 python_embeded，Linux 用系统 python）
├── jobs_scail/         # SCAIL int8：14 工作流 + inputs(参考图+7驱动) + submit_scail.py
├── jobs_ltx/           # LTX 2.5 int8：14 工作流 + 14 端点图 + submit_ltx.py(带冒烟)
├── tools/              # 18 个后处理脚本（纯 Python：稳腿/稳嘴/融化帧/伪影帧/QA/审计/组装）
├── install_linux.sh    # 一键：装依赖 + git clone 5 个插件 + 检查模型
├── e2e_test.sh         # ★ 端到端测试主脚本（7 阶段，冒烟不过即停）
├── run_all.sh          # 一键跑 SCAIL 套（起 ComfyUI + 提交 + 等完成）
└── README.md

models_int8/            ← 在 deploy/ 下、上传包**外**（58.2 GB，10 个模型）
                        传到租卡机后放到 comfyui-int8/models/ 即可

**custom_nodes/ 不在包里**——install_linux.sh 会自动 git clone 5 个插件
（ComfyUI-GGUF / Easy-Use / KJNodes / VideoHelperSuite / Impact-Pack，
GitHub 直连失败自动切国内镜像 ghfast.top / gitclone.com）。
```

## Linux 上怎么跑

```bash
# 1. 先传 comfyui-int8/（90MB，几分钟），到租卡机上装依赖
#    （torch CUDA 约 2.5GB 下载 + git clone 5 个插件）
bash install_linux.sh

# 2. 把 models_int8/（58GB）传到租卡机，放到 comfyui-int8/models/
#    （XFTP 拖过去后改名/就位：~/comfyui-int8/models/）

# 3. 跑 SCAIL int8（14 个基础段）
bash run_all.sh

# 4. SCAIL 完了跑 LTX 2.5 int8 过渡——先冒烟一条，通过再批量
./venv/bin/python jobs_ltx/submit_ltx.py --smoke     # 先跑 1 条验证 2.5 图
./venv/bin/python jobs_ltx/submit_ltx.py             # 冒烟通过后跑剩余 13 条
```

依赖：python3.10+、GPU（4090 24G / A6000 / A100）。install 用清华镜像；
机器在海外就删掉 install_linux.sh 里的 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

## 端到端测试（推荐用这个，别手动一步步）

```bash
bash e2e_test.sh          # 从阶段1顺序跑，每阶段按 y 确认
bash e2e_test.sh 3        # 或单跑某一阶段（依赖装好了从冒烟开始）
```

7 个阶段，**冒烟不过自动停**（不浪费租卡时间）：

| 阶段 | 内容 | 验收标准 | 耗时 |
|---|---|---|---|
| 1 环境自检 | GPU/python/磁盘/git/模型在位 | nvidia-smi 认出卡、磁盘 ≥70G | 1 分钟 |
| 2 装依赖 | torch CUDA + clone 5 插件 | install 退出码 0 | ~10 分钟 |
| 3 SCAIL 冒烟 | 起 ComfyUI → 单条 idle1_s1 → 验帧 | 产出 ~94 帧 704×1280 | 5~25 分钟 |
| 4 SCAIL 批量 | 14 条（后台提交） | 盯 `comfyui.log` | 1~2 小时 |
| 5 LTX 冒烟 | submit_ltx.py --smoke | 2.5 图首验，出 25 帧 | 5~15 分钟 |
| 6 LTX 批量 | 14 条过渡 | — | 20~40 分钟 |
| 7 产出验收 | 文件数/帧数/分辨率对账 | 14+14 在位、帧数=素材 | 2 分钟 |

阶段 7 生成 `pull_back/FILES_TO_PULL.txt`（拉回本机的清单）。

## ⚠ 两个必须知道的点

1. **LTX 2.5 图没在本机验证过**（34GB 权重跑不动 16G 卡）。所以 submit_ltx.py
   有 `--smoke` 模式：先提交 1 条等成功再批量。冒烟失败就停，别硬跑 14 条。
   冒烟失败的排查方向：2.5 VAE 与 LTXV 节点兼容性、显存不足。
2. **jobs_ltx 的端点图是旧基础段的**。SCAIL int8 重新生成基础段后，端点会变——
   拿到新的 14 个 SCAIL 产出后，先在本机跑产线选优（make_avatar --from-select
   会自动重新提取端点并重建 jobs_ltx 工作流），把新的 jobs_ltx 传到租卡机
   再跑 LTX。顺序：SCAIL int8 → 选优 → 提取新端点 → LTX 2.5 int8 → 后处理 → 上架。

## 跑完之后

| 产出 | 位置 | 去哪 |
|---|---|---|
| 14 个基础段 | `ComfyUI/output/scail2/e2egreet01_g_*_00001_.mp4` | 拷回本机 `output\scail2\`，跑 `make_avatar --from-select` |
| 14 个过渡 | `ComfyUI/output/gzt_e2egreet01_t_*2*_s801*.mp4` | 同上（放到 runs/e2egreet01_t 对应位置） |

后处理（tools/ 里的脚本：稳腿/稳嘴/融化帧修复/QA/身份审计/组装）在本机或
租卡机跑都行——纯 Python，租卡机已装好 opencv/numpy 的话直接能用；
QA 脚本额外需要 insightface（`pip install insightface`）。

## 排障

| 现象 | 处理 |
|---|---|
| install 报 torch 装不上 | 确认 `nvidia-smi` 正常（驱动/CUDA） |
| 插件 clone 失败 | install 已自动切镜像；三个源都失败就手动 `git clone`（README 有清单） |
| 某插件依赖失败 | install 已容忍单插件失败；5 个节点族都不缺就能跑 |
| submit 报连接失败 | ComfyUI 还在加载模型，等 30 秒重跑 |
| 产出文件名带 _00002_ | 正常（同前缀递增版号），以 submit 脚本打印路径为准 |
| LTX 冒烟失败 | 停，看 comfyui.log；确认修复后重跑，别批量 |
| 模型检查报缺 | XFTP 的 models/ 没传完，断点续传补齐再跑 |
| 想改端口 | `bash run_all.sh 8190` |
