# digital-human/llm — 数字人 LLM 对话包（从 LiveTalking 搬运适配）

为产品落地准备的独立 LLM 对话包：把 LiveTalking（`E:\LiveTalking\LiveTalking`）里
验证过的流式对话逻辑搬到本项目，去掉 WebRTC/avatar 依赖，改为纯回调的事件流。

## 文件对照（LiveTalking → 本包）

| 本包 | LiveTalking 来源 | 作用 |
| --- | --- | --- |
| `llm_service.py` | `services/llm_service.py` | 流式 LLM 客户端 + 进程级连接池复用 |
| `llm_memory.py` | `services/llm_memory.py` | 滚动窗口多轮对话记忆（标签不进历史） |
| `action_parser.py` | `services/action_parser.py` | 动作/模块标签剥离（流式，不阻塞） |
| `text_slicer.py` | `services/text_slicer.py` | 标点软边界切句（TTS 韵律单元） |
| `dialogue.py` | `llm.py` 的 `llm_response` | 对话编排：流→标签→切段→事件回调 |
| `personas.py` | `server/personas.py` + 动作规则 | CUG 小C 人设 + 模块推进规则 |
| `config.py` | `config.py` 的 LLM 段 | DeepSeek 默认配置（含默认 key） |
| `demo.py` | — | 命令行演示 + 离线自测 |

## LLM 提供方（默认 DeepSeek，与 LiveTalking 一致）

- 端点 `https://api.deepseek.com/v1`，模型 `deepseek-chat`
- `config.py` 里保留 LiveTalking 的默认 key；环境变量可覆盖：
  `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_TEMPERATURE`
- 本地推理端点（ Ollama / LM Studio / llama.cpp server，含本机 1919）
  自动识别：不需要 key、8 秒超时、不重试（避免连不上时首字等十几秒）
- 切云端模型（如 DASHSCOPE）：设 `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL` 即可

## 快速开始

```bash
# 离线自测（不打 API，验证标签剥离/切句/记忆）
python -m llm.demo --selftest

# 单轮对话（打 DeepSeek）
python -m llm.demo --once "你好"

# 交互对话
python -m llm.demo
```

## 在产品里使用

```python
from llm import stream_dialogue, ConversationMemory

memory = ConversationMemory()          # 每个会话一份
for ev in stream_dialogue("你好", memory=memory):
    if ev.type == "action":            # greet / idle
        avatar.startSpeaking(first=(ev.text == "greet"))
    elif ev.type == "segment":         # 切好的语音段（标点边界、无标签）
        tts.speak(ev.text)
    elif ev.type == "text":            # 正文分片（聊天气泡用）
        ui.appendText(ev.text)
    elif ev.type == "module":          # health/fatigue/emotion/companion
        voice_prompt_then_jump(ev.text)   # 语音提示 + 用户确认后跳转
    elif ev.type == "done":
        ui.finishMessage(ev.text)
    elif ev.type == "error":
        ui.showFallback()              # LLM 不可达：走本地兜底
```

## 标签体系（系统提示词约束 LLM 输出）

- `[greet]` / `[idle]`：动作标签，回复最开头，驱动数字人动作链
  （本段库只有 greet 段；idle 不播动作）
- `[MODULE:key]`：模块推进意图，正文之后另起一行，key ∈
  health/fatigue/emotion/companion；由调用方决定提示方式
- 标签都会被剥离，不进 TTS、不进聊天气泡、不进对话历史

## 与当前 Vue 前端的关系

前端陪伴模块现在走后端 `/python-api/companion/chat`（非流式，见
`Emotion-Detect-py/routers/companion.py`）。本包是同一套对话逻辑的
流式产品化版本：后续要做真语音交互（TTS 播报 + ASR 确认）时，
由后端路由或独立数字人服务改调本包，前端事件逐条转成
avatar 动作 / 语音 / 气泡 / 模块跳转即可。
