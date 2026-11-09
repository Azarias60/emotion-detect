# digital-human —— 实时数字人工作区

emotionDetect 的数字人总装车间：**实时引擎**（跑在页面里的）、**生产线**（照片→数字人）、
**段库生成工具链**（当初造素材用的）、**对话包**（将来实时陪伴用）都在这。

## 目录地图

| 目录 / 文件 | 角色 | 说明 |
|---|---|---|
| `livetalking/` | **实时引擎（38G，勿删）** | LiveTalking 全套：wav2lip 口型 + CosyVoice3 语音 + Whisper 识别 + DeepSeek 对话 + WebRTC 服务（8020）。形象素材在 `livetalking/data/avatars/`，音色投递在 `livetalking/data/drop/`，配对表 `livetalking/data/presets.json` |
| `studio/` | **照片→数字人生产线** | `qa_segments.py`（段质量门：人脸可检+嘴静）、`build_avatar.py`（拼接→人脸检测→段表）。用法见 `studio/README.md` |
| `generation/` | 段库生成工具链 | SCAIL/LTX 出片段用：`graphs/`（ComfyUI 工作流构建）、`recipes/`（提示词配方）、`pipeline/`（段拼接脚本）、`comfy/`（ComfyUI API 客户端）、`workflows/`（原始工作流 JSON）、`config.py`（路径总配）。详细文档 `generation/README.md` |
| `llm/` | 对话包 | 从 LiveTalking 搬来的流式对话逻辑（将来陪伴模块做实时对话用） |
| `tools/` | QA 工具 | `metrics_check.py`、`qa_strips.py`、`analyze_motion.py`（漂移/接缝/拼图） |
| `assets/` | 输入素材 | `ref_5186.jpg`（鬼子照片）、`pose/`（姿态驱动视频） |
| `runs/` | 运行产物 | 每次生产/核查的输出与历史备份（按日期分子目录） |
| `tts_service.py` | 备用 TTS 服务 | edge-tts（8199），旧版陪伴对话用；实时引擎走 CosyVoice3，一般不起 |

## 三条常用命令

```bat
REM 1) 起实时引擎（页面才能看到数字人；模型加载约 15 秒）
livetalking\run-8020.bat

REM 2) 照片→数字人（先备好段素材：一个目录，每个 mp4 是一个动作段/过渡）
livetalking\venv\Scripts\python.exe studio\qa_segments.py --segments <段目录> --out runs\qa_<名>
livetalking\venv\Scripts\python.exe studio\build_avatar.py  --segments <段目录> --avatar-id <英文id> --display-name <显示名>

REM 3) 注册配对：编辑 livetalking\data\presets.json（一项 = 形象 + 音色，声明式、不用改代码），
REM    重启 run-8020.bat，页面「数字人」芯片就多一项
```

## 当前数字人

| id | 显示名 | 形象 | 音色 |
|---|---|---|---|
| zzf | zzf | my_avatar | default（人物训练音色） |
| yy | yy | yy | zzf2 |
| guizi | 鬼子 | guizi（照片 5186E77A 生成，2881 帧） | default（先用 zzf 的音色） |

## 设计文档（在仓库 docs/ 下）

- `../docs/photo-to-digital-human-pipeline.md` —— 照片→数字人链路设计（第一原则：全链路闭嘴、只迁动作，嘴由 wav2lip 实时驱动）
- `../docs/digital-human-mouth-state-machine.md` —— 状态机段表规则

## 注意事项

- 引擎 venv 在 `livetalking/venv/`（torch / onnxruntime / ffmpeg 二进制都在里面），跑引擎相关脚本都用它
- `livetalking/data/avatars/<id>/ranges.json` 是状态机段表，硬规格见引擎 `state_machine.load_avatar_plan`（必须含 idle+talk 段、`_total`，动作名限 greet/encourage/praise/comfort/dance）
- 换形象/音色都在页面「数字人」芯片上操作：一项 = 一对，点一下两个一起换
