# LiveTalking 本地数字人

基于 **Wav2Lip + Fun-CosyVoice3 + Whisper** 的实时对话数字人。整条链路全部本地运行：Whisper 负责语音识别，任意 OpenAI 兼容 LLM 负责回复，Fun-CosyVoice3 在本机做流式声音克隆 TTS，Wav2Lip 负责口型渲染，无需依赖任何第三方 TTS / ASR 在线服务。

## 功能特性

- **统一浏览器入口**：轻量静态模式开箱即用，一键升级为 WebRTC 实时口型画面
- **全双工麦克风对话**：浏览器端 AudioWorklet 持续上传 16 kHz PCM，服务端能量 VAD 自动断句，新一句话自动打断上一句
- **状态机动作编排**：idle 循环、四个动作段（greet / encourage / praise / comfort）、talk 交替播放，配合 57 段过渡帧自然衔接
- **流式对话管线**：LLM 回复按句切分送入 CosyVoice3 流式生成，边生成边播放，首句低延迟
- **多种输出方式**：WebRTC、WHIP 推流（SRS）、RTMP、OBS 虚拟摄像头
- **本地声音克隆**：首次启动提取固定音色并缓存，之后直接复用
- **无头回归测试**：测试套件不加载大模型权重，可在 CI 中运行

## 系统架构

```text
浏览器 AudioWorklet (16 kHz PCM)
        │  WebSocket /ws/conversation
        ▼
服务端能量 VAD 断句 (StreamingEnergyVAD)
        │
        ▼
常驻 Whisper ASR (ResidentWhisper)
        │
        ▼
LLM 流式回复 (OpenAI 兼容接口, 默认 DeepSeek)
        │ 按句切分
        ▼
Fun-CosyVoice3 流式 TTS ──────────────┐
        │                             │ 轻量模式
        ▼                             ▼
Wav2Lip 口型渲染              二进制 PCM 直接回传浏览器
        │                     (静态立绘 / idle.mp4 播放)
        ▼
WebRTC (aiortc) / RTMP / 虚拟摄像头
```

两种前端模式共用同一个 WebSocket 和同一条 ASR → LLM 管线，语音合成分道：轻量默认模式默认用本地 CosyVoice3（稳定、无网络依赖；也可 `--default_tts edge` 切换微软内置音色，需联网且服务端不稳定），实时口型模式用本地 CosyVoice3 训练音色并做 Wav2Lip 口型渲染。

- **轻量默认模式**：页面显示静态立绘和待机视频，LLM/TTS 的流式增量以 PCM 块直接推给浏览器播放，不消耗 GPU 做 Wav2Lip 推理；人设是**暖暖**（`personas/nuan.md`），音色是童声。
- **实时口型模式**：转写文本交给 WebRTC 会话，走 Wav2Lip 实时渲染，由 aiortc 推送音视频轨道；人设是**数字关怀助手**（`personas/care.md`，无名字），音色是 `mp4/sound_ref.wav`（“初代佐菲杰克”男声）注册的 `live_spk`。两套人设与音色互相独立，切换模式即切换角色。

## 目录结构

```text
app.py                 Web 服务入口：加载 avatar、注册路由、启动 aiohttp
config.py              全部 CLI 参数与默认值
state_machine.py       动作编排状态机 + Whisper ASR 封装
llm.py                 LLM 流式回复 → 按句切分 → 送 TTS
registry.py            插件注册表
server/
├── routes.py          /human 等通用 HTTP API
├── rtc_manager.py     WebRTC 连接与 SDP 信令管理
├── webrtc.py          PlayerStreamTrack / HumanPlayer 媒体轨道
├── session_manager.py 会话单例管理（脱离 WebRTC 也保活 session）
├── browser_app.py     统一浏览器入口页与 /api/default/* 接口
└── microphone_stream.py  WebSocket 全双工语音对话（VAD + 常驻 Whisper + 打断）
web/                   前端：index.html / app.js / app.css / default-assets/，以及上游旧调试页
streamout/             输出插件：webrtc / rtmp / virtualcam
avatars/               数字人模型实现（wav2lip_avatar）
tts/                   TTS 插件（cosyvoice3）
tests/                 回归测试（不加载大模型权重）
data/                  full_ranges.json 过渡帧配置 + avatars/ 素材
models/                wav2lip.pth、Fun-CosyVoice3-0.5B、whisper
mp4/                   sound_ref.wav 声音克隆参考音频
```

## 环境

建议使用：

- Windows 10/11 或 Ubuntu 22.04+
- Python 3.10
- NVIDIA GPU，建议 12 GB 以上显存
- 与本机 CUDA 匹配的 PyTorch

安装依赖：

```powershell
python -m venv venv
venv\Scripts\activate
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
```

## 模型和素材

启动前确认以下路径存在：

```text
models/
├── wav2lip.pth
└── Fun-CosyVoice3-0.5B/

data/
├── full_ranges.json
└── avatars/
    └── my_avatar/
        ├── full_imgs/
        ├── face_imgs/
        └── coords.pkl

mp4/
└── sound_ref.wav
```

下载 Fun-CosyVoice3：

```powershell
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git cosyvoice_repo
huggingface-cli download FunAudioLLM/Fun-CosyVoice3-0.5B-2512 --local-dir models/Fun-CosyVoice3-0.5B
```

实时口型模式的音色由参考音频注册：默认就是项目自带的 **`mp4/sound_ref.wav`（“初代佐菲杰克”男声）**，文本固定为“光之国的家是他们的故乡，初代佐菲杰克来自这地方”。服务启动时用**当前模型**重新提取 prompt_text / speech token / mel / embedding，注册成音色 ID `live_spk`；注册结果按音频 sha256 追踪（标记写在 `data/voices/<spk_id>.json`），音频或文本一变就自动重注册，不变则复用缓存。轻量页使用独立的小男孩音色 `livetalking_default_boy_v4`（由 `mp4/boy_voice_ref.wav` 及其逐字文本注册），两个模式互不影响。

换实时口型音色：把录音放到任意路径，用 `--live_ref_file <文件> --live_ref_text "<与音频逐字一致的文本>"` 启动即可（文本不对音色一定失真）。不要再直接搬运旧流程产出的 `data/avatars/*/spk2info.pt` 音色缓存：那些缓存来自另一版 speech tokenizer（码本大小同为 6561 但码字映射不同，实测与当前分词器逐位一致率为 0），即使能出声音色也会走样；程序只在参考音频不可用时才把它当兜底尝试。

动作编排需要至少 1751 帧；完整过渡需要 `full_ranges.json` 引用的全部 6083 帧。素材不足时程序会在启动阶段给出明确错误，而不是在渲染中途越界。

## 快速开始

### Web 服务

Windows 可运行：

```powershell
run.bat
```

或直接执行：

```powershell
venv\Scripts\python app.py `
  --transport webrtc `
  --model wav2lip `
  --avatar_id my_avatar `
  --tts cosyvoice3
```

浏览器访问 `http://localhost:8010/`。统一入口默认显示轻量版 default 数字人，可使用麦克风或文字完成 ASR → LLM → CosyVoice3 对话；点击“进入实时口型模式”后，页面会按需建立 WebRTC 会话并切换到 Wav2Lip 实时口型画面。返回轻量模式时会自动释放 WebRTC 连接。

浏览器不会先录制 WebM/M4A 文件。AudioWorklet 会把麦克风持续转换为 16 kHz PCM，经同一个 WebSocket 发送；服务端常驻 Whisper 并用能量 VAD 在约 0.6 秒停顿后自动开始识别。新一句话会打断上一句并清除旧音视频队列。

旧调试页面仍保留：

- `http://localhost:8010/webrtcapi.html`
- `http://localhost:8010/dashboard.html`

WebRTC 服务端需要开放 TCP 8010 和所需 UDP 端口。

`http://localhost:8010/` 和 `http://127.0.0.1:8010/` 是本机回环地址，只能从运行服务的电脑访问。同一局域网内的其他设备需要使用服务电脑的 IPv4 地址，例如 `http://192.168.1.20:8010/`，并在 Windows 防火墙中允许 TCP 8010 及 WebRTC 使用的 UDP 流量。普通 HTTP 的局域网地址通常不能取得浏览器麦克风权限；需要远程使用麦克风时应配置 HTTPS。跨公网访问还需要处理路由器 NAT、防火墙、HTTPS 和 TURN，不能直接把 `localhost` 发给别人使用。

## 状态机动作编排

`my_avatar` 素材使用统一的 6083 帧时间线，划分为 9 个状态段：

```text
idle1 / idle2 / idle3 随机完整循环
                 │
                 │ 收到回复和动作
                 ▼
       idle → action 过渡
                 │
                 ▼
      greet / encourage / praise / comfort
                 │
                 ▼
          action → talk 过渡
                 │
                 ▼
        talk ⇄ talk2（长回复循环）
                 │
                 ▼
           talk → idle 过渡
```

行为细节：

- 动作请求不会截断当前 idle：在 idle 播放中收到动作请求时先排队，等当前段落播完再过渡。
- 语音开始时若没有排队动作，数字人立即从 idle 过渡到 talk 开始口型播报；有排队动作时动作优先，动作段播完自动接 talk。
- 过渡帧段从 `data/full_ranges.json` 加载，切换前同步解码前若干帧防止卡顿，同时后台预取目标片段。
- talk 播完若仍在说话则在 talk ⇄ talk2 之间交替，说话结束后回 idle。
- **LLM 语义驱动动作**：系统提示词（自动追加，人设无需重复声明）要求 LLM 在回复开头输出动作标签 `[greet]` / `[encourage]` / `[praise]` / `[comfort]`（普通闲聊用 `[idle]`）。服务端在流式输出中零延迟剥离该标签：实时口型模式据此触发状态机对应动作段（greet→4、encourage→5、praise→6、comfort→7），轻量模式向前端发送 `action` 事件并在对话区显示动作提示；标签本身不会被朗读或显示。
- **语音分工**：轻量默认页默认使用本地 CosyVoice3（与实时口型同源、稳定不依赖网络；`--default_tts edge` 可切回在线音色）。任一引擎某段合成失败时自动原地重试一次，仍失败则该段改用本地 Windows 语音并向前端发送 `tts_fallback` 提示，保证回复不会无声。
- Web API 模式不启用音频缓冲，普通 `/human` 请求会立即播报。
- 服务启动后在后台线程预热 CosyVoice3（加载模型 + 实时口型训练音色 + 轻量页童声 + 一次试合成），第一条回复不再承担约 20 秒的模型冷加载。

## API 一览

### HTTP 接口

| 端点 | 方法 | 功能 |
|---|---|---|
| `/offer` | POST | WebRTC SDP offer/answer 信令 |
| `/human` | POST | 文本输入，`type=echo` 直接 TTS、`type=chat` 走 LLM，支持 `interrupt` 打断 |
| `/humanaudio` | POST | 上传音频文件直接播放（multipart，字段 `file` + `sessionid`） |
| `/set_audiotype` | POST | 设置动作编排状态（`audiotype`） |
| `/interrupt_talk` | POST | 打断当前说话并清空输出缓冲 |
| `/is_speaking` | POST | 查询是否正在说话 |
| `/api/default/config` | GET | 前端默认模式配置（VAD 阈值、待机延时等） |
| `/api/default/health` | GET | 健康检查（avatar、tts、默认素材是否齐全） |
| `/api/personas` | GET | 人设列表与当前激活人设 |
| `/api/persona` | POST | 切换激活人设（`{"id": "xiaonuan"}`），同时切换提示词与音色 |
| `/api/assets` | GET | 人物与音色素材库：可用人物、可切换音色、待生成视频、生成任务状态 |
| `/api/voice` | POST | 切换实时口型音色（`{"id": "teacher"}` 或 `"default"`），下一句生效 |
| `/api/avatar/generate` | POST | 把 `data/drop/<名字>.mp4` 后台生成为可切换人物（人脸检测+裁剪） |

除 `/offer`、`/api/default/*` 外，接口均需携带 `sessionid` 定位目标会话；返回格式统一为 `{"code": 0, "msg": "ok", "data": ...}` 或 `{"code": -1, "msg": "错误信息"}`。

### WebSocket `/ws/conversation`

全双工语音对话通道，同一个连接内同时传输 JSON 控制消息和二进制 PCM。

上行（客户端 → 服务端）：

| 消息 | 说明 |
|---|---|
| `{"type": "configure", "mode": "default"|"live", "sessionid": "..."}` | 设置模式；`live` 模式需带 WebRTC 会话 ID |
| `{"type": "text", "text": "..."}` | 直接发送文本（跳过 ASR） |
| `{"type": "cancel"}` | 打断当前回复 |
| 二进制帧 | 16 kHz Int16 PCM 麦克风数据 |

下行（服务端 → 客户端）：

| 消息 | 说明 |
|---|---|
| `{"type": "ready", "sampleRate": 16000}` | 连接就绪 |
| `{"type": "configured", "mode": "...", "wakeRequired": bool, "wakeWord": "..."}` | configure 生效（含该模式唤醒门控状态） |
| `{"type": "speech_started"}` / `{"type": "speech_stopped"}` | VAD 检测到人声开始 / 结束 |
| `{"type": "wake_word_pending", "text": "..."}` | 未唤醒：这轮语音未含唤醒词，已被丢弃 |
| `{"type": "wake", "word": "..."}` | 唤醒成功，对话开始 |
| `{"type": "pipeline", "stage": "asr"|"llm"}` | 管线阶段指示 |
| `{"type": "transcript", "text": "..."}` | 识别（或接收）到的用户文本 |
| `{"type": "assistant_delta", "text": "..."}` | LLM 回复流式增量（default 模式） |
| `{"type": "action", "action": "greet"|"encourage"|"praise"|"comfort"}` | LLM 识别出的语义动作（default 模式提示；live 模式同时驱动数字人动作段） |
| `{"type": "tts_skip"}` | 在线 TTS 重试后仍失败，本段未朗读（default 模式，文字回复不受影响） |
| `{"type": "audio_start"}` | 即将开始发送音频 |
| 二进制帧（首字节 `0x01`） | 512 样本 PCM 语音块（default 模式） |
| `{"type": "response_accepted"}` | live 模式回复已提交给数字人 |
| `{"type": "response_done"}` | 本轮回复完成 |
| `{"type": "error", "message": "..."}` | 错误信息 |

打断机制：每轮回复携带一个代际标记（generation），用户再次开口或发送 `cancel` 时代际加一，发送线程自动丢弃所有过期代际的音频与消息，实现即时打断且不残留旧回复。

话尾宽限：VAD 判定说完后回复并不会立刻发出，而是等待 `--mic_reopen_grace_ms`（默认 800ms）。如果用户在这段时间内继续开口（句中换气、补充后半句），已结束的音频会并回同一句话重新断句，不会抢答也不会把一句话拆成两轮对话。

唤醒词门控：麦克风通道在说出唤醒词之前不进入对话——两个模式统一用「你好」激活。命中后唤醒词从语句中剥离（`你好，我考砸了` → `我考砸了`；「你好呀/您好/您好呀」等常见变体同样命中），只说唤醒词本身则视为一句问候；未命中的语音轮被静默丢弃并向前端发送 `wake_word_pending` 提示。唤醒一次后持续对话，切换模式时门控重新待命。文字输入框不经过门控。`--wake_word_default` / `--wake_word_live` 可改词，留空禁用。

## 人物与音色素材库

实时口型页面的"人物与音色"面板可以随时切换数字人形象和说话声音。新素材统一放进投递文件夹 `data/drop/`（文件夹内有中文说明 `README-先读我.txt`）：

```text
data/drop/
├── xiaoming.mp4      人物视频：点面板里的【生成】后台预处理成 data/avatars/xiaoming/
├── teacher.wav       音色参考音频（5~10 秒清晰人声）
└── teacher.txt       同名逐字文本（UTF-8，必须与音频内容一字不差）
```

- **切换人物**：面板点人物名即切换（WebRTC 自动重连）。随手拍的视频没有待机/动作/过渡段落，切换后为"循环播放 + 口型"模式，不启用动作编排（帧数 ≥1751 的专门素材才带动作，列表里会标注）。
- **切换音色**：面板点音色名即切换，下一次开口生效，无需重连；`default` 为人物自带训练音色。音色注册复用 sha256 指纹缓存，音频/文本变化自动重注册。
- **yy ↔ zzf2 配对**：自带人物 **yy**（原 `data/avatars/zzf2` 素材改名，1077 帧，配 `data/avatars/yy/ranges.json` 段表：idle→comfort→talk 编排，支持 `[comfort]` 动作）与遗留训练音色 **zzf2**（`data/voices/zzf2_spkinfo.pt`，导入时自动补 `<|endofprompt|>` 前缀）。切换到 yy 会自动选中 zzf2 音色，之后仍可手动改。zzf2 缓存来自旧版 speech tokenizer，音色如有走样可回退默认音色。
- **素材黑名单**：`zzf1` 等更早的遗留素材在枚举、切换、`/offer` 构建会话三处统一拦截，永不出现、永不加载。
- 生成任务在后台子进程执行（`avatars/wav2lip/genavatar_stream.py`），面板每 3 秒轮询进度，日志在 `data/drop/_gen/<名字>.log`。

## 人设（Persona）

`personas/` 目录下每个 `.md` 文件是一个人设，frontmatter 元信息 + 正文提示词：

```markdown
---
name: 小勇
description: 元气满满的学习伙伴
mode: live                            # 可选：default（轻量页）/ live（实时口型）/ any（通用）
ref_wav: personas/xiaoyong/ref.wav    # 可选：零样本音色参考音频
ref_text: 参考音频对应的精确文本        # 可选
instruct: 用低龄小男孩的声音说话       # 可选：自然语言音色指令（无需参考音频）
hotwords: 换换=小勇                    # 可选：ASR 热词纠错
---
你是一个元气满满的数字人伙伴……
```

两个前端是两套人设：**每个模式各自维护"当前人设"**，`mode` 声明该人设服务哪个前端（默认 `any`）。仓库自带两套——`personas/nuan.md`（暖暖，`mode: default`，初始界面/轻量页）和 `personas/care.md`（数字关怀助手，`mode: live`，实时口型，无名字）。页面 chips 只列出当前模式可用的人设，点击切换只影响该人设所属模式。

实时口型的音色优先级：人设参考音频（零样本克隆）> 人设 instruct 指令 > 参考音频 `mp4/live_ref.wav` 注册的 `live_spk` > 兜底固定音色 `livetalking_fixed_voice`；轻量页固定使用自己的童声，不受人设影响。带参考音频的人设首次使用时自动提取音色并缓存到模型目录 `spk2info.pt`，无需重启。

## 配置 LLM

项目当前在 `config.py` 中配置了默认 LLM 密钥，可以直接启动。环境变量的优先级更高，可用于临时覆盖默认配置：

```powershell
$env:LLM_API_KEY = "你的密钥"
$env:LLM_BASE_URL = "https://api.deepseek.com/v1"
$env:LLM_MODEL = "deepseek-chat"
```

也可以通过 `--llm_api_key`、`--llm_base_url` 和 `--llm_model` 传入。接口需兼容 OpenAI Chat Completions API。

默认密钥以明文保存在本地代码中。提交到公开仓库或把项目发送给他人前，请删除 `DEFAULT_LLM_API_KEY` 的值并及时轮换密钥。

## 常用参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--model` | `wav2lip` | 数字人模型（musetalk/ultralight 需自行实现） |
| `--avatar_id` | `my_avatar` | `data/avatars` 下的素材 ID |
| `--batch_size` | `8` | 推理批大小 |
| `--modelres` | `256` | 模型分辨率 |
| `--transport` | `webrtc` | 输出方式：`webrtc` / `rtcpush` / `rtmp` / `virtualcam` |
| `--push_url` | 本机 SRS WHIP 地址 | `rtcpush` 模式的推流地址 |
| `--listenport` | `8010` | Web 服务监听端口 |
| `--max_session` | `1` | 最大并发会话数 |
| `--tts` | `cosyvoice3` | 实时口型模式的 TTS 插件 |
| `--default_tts` | `cosyvoice3` | 轻量页 TTS 引擎：`cosyvoice3`（本地稳定）/ `edge`（微软内置音色，需联网） |
| `--default_tts_voice` | `zh-CN-YunxiNeural` | 轻量页 Edge TTS 音色（仅 `--default_tts edge` 时生效） |
| `--cosyvoice3_model_dir` | `models/Fun-CosyVoice3-0.5B` | Fun-CosyVoice3 本地目录 |
| `--TTS_SPK_ID` | `livetalking_fixed_voice` | 兜底缓存音色 ID（找不到实时口型音色时使用） |
| `--live_spk_id` | `live_spk` | 实时口型模式使用的 CosyVoice3 音色 ID |
| `--live_ref_file` | `mp4/sound_ref.wav` | 实时口型音色的参考音频（默认“初代佐菲杰克”男声）；启动时用当前模型注册，按 sha256 追踪变更 |
| `--live_ref_text` | `光之国的家是他们的故乡，初代佐菲杰克来自这地方` | 参考音频的逐字文本，必须与音频一致 |
| `--live_spk_cache` | 空 | 回退用旧音色缓存路径；留空取 `data/avatars/<avatar_id>/spk2info.pt`（不推荐） |
| `--tts_speed` | `1.0` | 语速倍率 |
| `--llm_base_url` | `https://api.deepseek.com/v1` | OpenAI 兼容接口地址 |
| `--llm_api_key` | 环境变量 / 内置默认值 | LLM 密钥 |
| `--llm_model` | `deepseek-chat` | LLM 模型 |
| `--llm_system_prompt` | `你是一个友好的数字人助手。` | 系统提示词 |
| `--asr_model` | `small` | Whisper 模型：tiny/base/small/medium/large |
| `--asr_backend` | `openai-whisper` | ASR 后端 |
| `--mic_vad_threshold` | `0.018` | 服务端麦克风人声能量阈值 |
| `--mic_vad_start_ms` | `120` | 连续人声达到该时长后开始一句话 |
| `--mic_vad_silence_ms` | `600` | 连续静音达到该时长后自动提交 |
| `--mic_max_utterance_s` | `20` | 单句话最长录音时间 |
| `--mic_reopen_grace_ms` | `800` | 话尾宽限：该时长内继续开口会并回同一句话，避免抢答丢字 |
| `--wake_word_default` | `你好` | 轻量页麦克风唤醒词，留空禁用 |
| `--wake_word_live` | `你好` | 实时口型页麦克风唤醒词，留空禁用 |
| `--webrtc_video_queue_size` | `12` | WebRTC 视频有界抖动缓冲帧数（约 480ms） |
| `--webrtc_audio_queue_size` | `24` | WebRTC 音频有界抖动缓冲包数（约 480ms） |
| `--webrtc_preroll_ms` | `120` | 音视频共用的首播预缓冲时间 |
| `--initial_preload_frames` | `32` | 新实时会话首屏同步预载帧数 |

## 测试

不加载大模型的回归测试：

```powershell
venv\Scripts\python -m unittest discover -s tests -v
```

语法检查：

```powershell
venv\Scripts\python -m compileall -q .
```

测试覆盖：WebRTC 音视频时钟同步与抖动缓冲、状态机流转与过渡片段加载、能量 VAD 断句、CosyVoice3 流式切块、参数默认值锁定。其中 TTS 测试会 import `tts.cosyvoice3`（间接导入 torch/modelscope），需在装好依赖、repo 根目录下运行；所有测试都不加载权重文件。

## 常见问题

- 首次启动较慢：Wav2Lip 和 CosyVoice3 需要加载模型；只有第一次会提取固定音色，之后直接读取缓存。
- 提示缺少 `flash-attn`：Windows 上可以忽略，程序会使用标准 PyTorch 注意力实现。
- CosyVoice3 代码位于 `cosyvoice_repo`，模型位于 `models/Fun-CosyVoice3-0.5B`；两者缺失时程序会给出明确错误。
- 没有 LLM 密钥：页面会提示 LLM 调用失败；请设置 `LLM_API_KEY`。
- 显存不足：降低 `--batch_size`。
- 浏览器拿不到麦克风权限：`localhost` 之外的普通 HTTP 地址会被浏览器拒绝麦克风，需配置 HTTPS 或改在本机访问。

## 许可

代码使用 Apache-2.0 License。基于本项目制作并发布的视频请保留 LiveTalking 标识，并遵守模型、声音和人物素材各自的授权要求。
