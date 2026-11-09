> **位置说明**：本文档描述「段库生成工具链」（SCAIL-2 动作迁移 + LTX-2.3 首尾帧过渡）。
> 2026-10-20 目录整理后，该工具链已并入 `generation/`，本文档随工具链一起放在这里；
> digital-human 的总导航见上级目录的 `../README.md`。

# digital-human — 数字人视频生成（SCAIL-2 动作迁移 + LTX-2.3 首尾帧过渡）

emotionDetect（CUG 多模态健康与情绪智能检测系统）的**情感数字人出片模块**，
2026-10-10 由独立项目 `video-gen` 并入。当前固化配方为 **v28**：一条命令产出
`idle1 → 过渡 → greet` 三段拼接视频，全程 **704×1280 @ 20fps 原生输出，零归一化**
（不缩放、不转帧率、不重编码拼接）。

## 在 emotionDetect 中的接入关系

```text
情绪/疲劳/健康检测（Emotion-Detect-py）
        │  检测结果（情绪占比、疲劳分级…）作为选段/触发输入
        ▼
┌─────────────────────┐      ┌──────────────────────────┐
│ 情感数字人页（前端）  │ ◄─── │ digital-human（本目录）   │
│ CompanionDetect.vue  │ 段库  │ SCAIL-2+LTX-2.3 出片     │
└─────────────────────┘      └──────────────────────────┘
```

- **本目录是离线出片工具链**：不参与 emotionDetect 运行时（FastAPI:8081 / Vue），
  需要产出或更新数字人表演段时手动跑；生成算力由 ComfyUI 便携版（:8188）提供。
- **产出物两层消费**：①  emotionDetect 数字人段的段库（idle/greet/talk + 过渡）；
  ②  同名段也可直接进 `E:\LiveTalking\LiveTalking\<名字>-character\`（LiveTalking
  主项目的状态机/段表/形象构建直接可用，两边段命名一致）。
- 目录内 `.gitignore` 只管自己的缓存；`*.mp4` 被上层 .gitignore 忽略，
  已按仓库惯例为 `assets/pose/` 的复跑必需素材开例外（见仓库根 .gitignore）。

## 流水线

```
姿态视频(30fps)                参考图(5186)
     │                            │
     ▼                            ▼
┌─────────────┐            ┌─────────────┐
│  SCAIL-2    │◄───────────│  人物+背景   │
│  动作迁移    │   replacement_mode=false  │
│  force_rate │            └─────────────┘
│  =20 → 20fps│
└──────┬──────┘
       │ greet 成片段（反抖动提示词）
       ▼
  idle 末帧 + greet 首帧
       │
       ▼
┌─────────────┐
│  LTX-2.3    │  双端 guide 0.85、无中间锚点、81 帧(4.05s)
│  首尾帧过渡  │  多 seed 提交 → 单帧尖峰最小者当选
└──────┬──────┘
       ▼
  ffmpeg -c copy 无损拼接 → 交付目录 + QA 拼图
```

## 环境

- Python：`E:\ZCodeWorkspace\venv\Scripts\python.exe`（已含 opencv/numpy/requests/imageio-ffmpeg）
  或任意装了 `requirements.txt` 的 3.9+ 环境。**不要用 emotionDetect / LiveTalking 的 venv**
  （本模块不依赖它们；cv2 5.0 不带 haarcascade 数据文件，已自带 `tools/data/`）。
- ComfyUI 便携版需在跑（API `http://127.0.0.1:8188`），模型按 `graphs/` 里的文件名装好。

## 快速开始

```bash
cd E:\emotionDetect\digital-human

# 复跑当前配方（3D 人物参考图 + 反抖动 + 57帧过渡 + 接缝色彩匹配，v32 实证）
E:\ZCodeWorkspace\venv\Scripts\python.exe -m pipeline.make_chain --run v32 \
    --ref-image "E:\Comfyui\可爱3D人物页面导览.png" --scail-recipe scail_3d_antijitter.json

# 换新 greet 姿态视频（本地文件会自动拷进 ComfyUI input）
E:\ZCodeWorkspace\venv\Scripts\python.exe -m pipeline.make_chain --greet-pose D:\new_greet.mp4 --run v33

# 底段已存在，只重跑过渡+拼接
E:\ZCodeWorkspace\venv\Scripts\python.exe -m pipeline.make_chain \
    --idle <idle.mp4> --greet <greet.mp4> --run v34
```

产出（默认交付到桌面 `动作替换验证\目标_idle过渡greet\`，`--out` 可改）：

| 文件 | 内容 |
|---|---|
| `完整衔接_<run>.mp4` | 三段拼接主交付（163 帧 / 8.15s @20fps，过渡 57 帧=2.85s） |
| `<run>_1_idle.mp4` / `_2_过渡.mp4` / `_3_greet.mp4` | 分段落盘（过渡段为色彩匹配后版本） |
| `QA_master.png` / `QA_seams.png` | 总览条 + 接缝对比 |

接缝上下文/表情特写/全段密抽等完整 QA 拼图（给 GLM-5.3-Flash 视觉验收用）：

```bash
E:\ZCodeWorkspace\venv\Scripts\python.exe tools/qa_strips.py \
    --idle <idle.mp4> --trans <过渡.mp4> --greet <greet.mp4> --concat <拼接.mp4>
```

运动指标（帧差三段/尖峰/抖动指数/接缝差）：

```bash
E:\ZCodeWorkspace\venv\Scripts\python.exe tools/analyze_motion.py <拼接.mp4> --seam-at 61 142
```

## 配方（改词改参只动 recipes/，不碰代码）

| 文件 | 管什么 |
|---|---|
| `recipes/ltx_transition_v28.json` | LTX 过渡提示词（pos/neg/nag）、帧数 57（2.85s≈3s，8k+1 约束）、双端 guide 0.85、seed 列表、`color_match` 接缝色彩匹配开关 |
| `recipes/scail_antijitter_v28.json` | SCAIL 真人反抖动提示词、force_rate=20、输出 fps=20 |
| `recipes/scail_3d_antijitter.json` | SCAIL 3D 人物版提示词（外观描述中性化，配 3D 参考图） |
| `recipes/scail_{greet,idle}_base.json` | SCAIL 图底快照（用户 ComfyUI UI 验证参数导出，49 节点原样） |

`graphs/scail.py` 只允许覆盖 6 个输入点：提示词(3/4)、参考图(30)、姿态视频与
force_rate(33)、帧数上限(220)、fps(177)、输出前缀(176)。其余拓扑（模型、采样器、
窗口循环、ColorTransfer）一律保持用户原值——**replacement_mode 保持 false**，
开启会在画面里出现两张脸。

## 已知坑（都是实片踩出来的，改配方前先读）

1. **NVENC 写延迟**：视频刚生成完立刻用 cv2 读会读到 0 帧。`comfy/api.py:frames_of`
   带 8 次重试，别去掉。
2. **Wan/SCAIL 帧数须 4n+1**：输出帧数会被隐式修剪（如 71 帧@30fps → force_rate 20 →
   47 → 45 帧）。想要整段就别在源头上裁成非 4n+1 长度。
3. **LTX 帧数须 1+8n**（v31 起默认 57 帧=2.85s≈3s；3s@20fps=60 帧不合法，官方会被静默吸附到最近合法值）。加长过渡段可缓解尾部收敛抖动（v24→v28 实证），缩短要以尖峰指标验证。
   **实测下限就是 57**：9/17/25/33 帧全部不可用——帧数不足时前段贴首帧静止、结尾硬切尾帧 guide，尾部出现 19~33 的单帧全画面跳变（25 帧还叠加奇偶交替闪烁）。不要试图压短过渡段。
4. **LTX 提示词必须正向描述**（官方规范：主动作开头、字面精确、≤200 词）。v30 用"He does not walk/step/turn…"否定句墙写严格动作限制，结果全画面单帧跳帧（尖峰 11.48 vs v29 的 5.01）；v31 改回正向描述、严格性全部放 neg 后尖峰 3.68、中段运动量几乎减半。禁止的具体动作写 neg，pos 只写"拍的是什么"。
5. **Q4 量化模型的渲染域偏差**：LTX-2.3 Q4_K_M 出来的人比 SCAIL 底段小约 6%、
   暗约 4~6L，接缝处有可见台阶。提示词治不了；官方渠道无修（ComfyUI-LTXVideo #534
   是 `--fp16-vae` 启动参数问题，与量化无关，本机未加该参数）。**当前解法：配方
   `color_match: true` 的自校准 offset 匹配**（`comfy/api.py:color_match_transition`，
   v32 实证 seam 残差 4 色阶 → ≤1.1）。要治本换官方 bf16 整模型（22GB，16GB 卡需重度 offload）。
6. **色彩校正管道的编码漂移**：重编码过渡段若直接喂 bgr24 给 ffmpeg，swscale 默认矩阵
   转换会让暗场景每过一次暗 1~2 色阶，把校正量吃掉（v1~v4 实测残差卡在 2.5+）。必须
   cv2 自转 I420 再喂 rawvideo（`api.encode_video_i420`），且先编码一遍未校正帧实测
   漂移烘进 delta（自校准，v5 实证残差 ≤1.2）。
7. **guide 强度**：双 0.85 是实片最优。0.7 不受提示词控制；中间锚点会让头部冻住 8 帧（已删）。
8. **中文路径**：cv2.imread/imwrite 对中文路径静默失败，一律走 imdecode/`api.imwrite_u`（imencode+二进制写）。
9. **抖动指标不可跨版本直接比**：帧数不同（45 vs 57）时 jitter_index 分母不同，只比单帧尖峰；3D 内容对比度高，各段 diff 天然比真人版大，跨参考图比较看接缝色差和尖峰性质（孤立尖峰=跳帧，驼峰=正常动作）。

## 目录

```
digital-human/
├── config.py            # 全局路径（ComfyUI 目录、规格、交付目录、ffmpeg）
├── comfy/api.py         # ComfyUI REST 客户端 + 读帧/帧差/抖动指数 + I420 编码/接缝色彩匹配
├── graphs/ltx_flf.py    # LTX-2.3 首尾帧图构建（v28 接线）
├── graphs/scail.py      # SCAIL-2 图构建（白名单式输入覆盖，支持 --ref-image）
├── pipeline/make_chain.py   # 主流程：SCAIL → 端点帧 → LTX 多 seed → 色彩匹配 → 拼接 → QA
├── tools/qa_strips.py       # Flash 视觉验收拼图
├── tools/analyze_motion.py  # 运动指标分析
├── recipes/             # 提示词与参数配方（JSON，可手改；LTX 配方带 color_match 开关）
├── workflows/           # ComfyUI 原始工作流 JSON 备份（LTX/SCAIL×2）
├── assets/              # 参考图 + 已验证姿态视频（greet 手裁版 / idle1）
└── runs/                # 每次运行的中间产物（端点帧、色彩匹配校准与成品）
```
