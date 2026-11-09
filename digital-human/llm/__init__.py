# -*- coding: utf-8 -*-
"""数字人 LLM 对话包（从 LiveTalking 搬运适配，为产品落地准备）。

与 LiveTalking 的对应关系：
- llm_service.py    <- services/llm_service.py（流式客户端 + 进程级复用）
- llm_memory.py     <- services/llm_memory.py（滚动窗口多轮记忆）
- action_parser.py  <- services/action_parser.py（动作标签剥离）
- text_slicer.py    <- services/text_slicer.py（TTS 软边界切句）
- dialogue.py       <- llm.py 的 llm_response 编排（去掉 WebRTC/avatar 依赖，改纯回调）
- personas.py       <- server/personas.py + 动作规则（CUG 小C 人设 + 模块推进规则）

适配差异：
- 默认 LLM 用 DeepSeek（api.deepseek.com/v1, deepseek-chat），保留 LiveTalking
  配置里的默认 key，支持环境变量覆盖；本地推理端点（1919）自动降级为无需 key。
- 动作标签词汇按本项目的段库裁剪：只有 greet / idle（段库里有 greet 段和
  idle1/2/3；没有 encourage/praise/comfort 段，标签出现时静默降级为不播动作）。
- 新增模块推进意图 [MODULE:key]：LLM 判断用户需要某个检测模块时输出，
  由调用方决定是语音提示还是直接跳转（本项目用语音提示+确认后跳转）。
"""

from .config import LLM_CONFIG
from .dialogue import DialogueEvent, stream_dialogue
from .llm_memory import ConversationMemory
from .llm_service import LLMService
from .text_slicer import SegmentSlicer

__all__ = [
    "LLM_CONFIG",
    "LLMService",
    "ConversationMemory",
    "SegmentSlicer",
    "DialogueEvent",
    "stream_dialogue",
]
