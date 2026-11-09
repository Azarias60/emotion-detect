# -*-coding: utf-8 -*-
"""情感陪伴数字人：大模型对话 + 模块推进。

设计：
- 人设：CUG 多模态健康检测系统的陪伴数字人，语气温和、口语化、简短（2-4 句）。
- 模块推进：系统提示词里内置四个检测模块；模型判断用户诉求与某模块相关时，
  在回复末尾输出一行机读标签 [MODULE:key]，后端解析成建议卡片数据，
  前端在用户确认后跳转。用户没确认前绝不自动跳转。
- 本地 LLM（Ternary-Bonsai-2-27B，OpenAI 兼容）不可用时走本地兜底回复，
  保证数字人永远有话说。
"""
import re

from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.logger import get_logger
from core.responses import success
from services.ai_service import _chat_completion, _extract_content
from core.config import AI_CONFIG

logger = get_logger(__name__)


def _companion_client():
    """陪伴对话专用客户端：本地 LLM 未启动时快速失败（不重试、8 秒超校），
    避免每次请求都等默认重试把首字延迟拖到十几秒。"""
    from openai import OpenAI

    return OpenAI(
        base_url=AI_CONFIG["base_url"],
        api_key=AI_CONFIG.get("api_key") or "local",
        timeout=8.0,
        max_retries=0,
    )

router = APIRouter(tags=["情感数字人"])

# 与前端路由一致的模块表（key 必须和前端 MODULES 的 key 一致）
MODULES = {
    "health": {"title": "健康问诊", "path": "/detect/health",
               "desc": "舌象、心率与综合分析",
               "points": ["舌象与面色分析", "心率与血氧趋势", "综合健康建议"]},
    "fatigue": {"title": "疲劳检测", "path": "/detect/fatigue",
                "desc": "眨眼、哈欠与疲劳评估",
                "points": ["眨眼频率与时长", "哈欠与打盹识别", "疲劳分级与休息建议"]},
    "emotion": {"title": "情绪识别", "path": "/detect/emotion",
                "desc": "表情、语音与情绪状态",
                "points": ["面部表情与微表情", "语音情绪识别", "情绪占比与趋势"]},
    "companion": {"title": "情感数字人", "path": "/detect/companion",
                  "desc": "陪伴对话与心理支持",
                  "points": ["随时语音对话", "情绪疏导与陪伴", "重要资料展示与保存"]},
}

MODULE_LINES = "\n".join(
    f"- {key}：{m['title']}（{m['desc']}），路径 {m['path']}" for key, m in MODULES.items()
)


DOC_RULES = """
输出规则（机读标签，用户看不到，必须严格遵守）：
- 当用户询问检测结果、健康数据、指标解释等需要完整展示的重要资料时，在回复最后单独一行输出：
  [DOC: 资料标题 | 要点1;要点2;要点3]
  要点 3~6 条，每条一句话、具体可用（含数值与结论），不要空话。
- 当用户明显想去做某项检测（如说"想查疲劳"）时，输出：[MODULE: fatigue]（可选 health / emotion / companion）
- 普通闲聊不要输出任何标签。
"""

SYSTEM_PROMPT = f"""你是"CUG 多模态健康检测系统"的陪伴数字人，名字叫小C。
你此刻站在系统首页中央，用语音和画面陪伴用户。

性格与说话规则：
- 语气温和、真诚、口语化，像身边的朋友，不像客服。
- 每次回复 2-4 句，不超过 80 字，不要长篇大论。
- 不堆砌专业术语；涉及健康话题时提醒"检测结果仅供参考，不以诊断代替医生"。
- 用户明显难过、焦虑时先安抚，再谈其他。
- 不知道的信息不编造。

模块推进规则（重要）：
系统有四个检测模块：
{MODULE_LINES}

当用户的诉求明显与某个模块相关（例如说"我最近总犯困"→疲劳检测，
"帮我看看心情/情绪"→情绪识别，"舌苔/心率/身体不舒服"→健康问诊），
你在正常回复用户之后，另起一行输出机读标签：[MODULE:模块key]
- 只在确有匹配时输出，一次最多一个。
- 模块key 只能取 health / fatigue / emotion / companion 之一。
- 用户只是闲聊、打招呼、道别时不输出标签。
- 不要向用户提起这个标签本身，前端会把它渲染成一张建议卡片，由用户决定是否前往。
""" + DOC_RULES

FALLBACK_REPLIES = [
    "我在听呢，你慢慢说，不急。",
    "嗯嗯，我在。要不要跟我多聊聊最近的情况？",
    "谢谢你愿意跟我说。如果哪里不舒服，也可以做个检测看看。",
]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    history: list[dict] = Field(default_factory=list)
    first_utterance: bool = False


def _build_messages(req: ChatRequest) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in req.history[-8:]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:300]})
    messages.append({"role": "user", "content": req.message[:500]})
    return messages


def _parse_module(text: str):
    """分离机读标签，返回 (给用户看的文本, 资料卡/模块卡)。

    支持两种标签（都不给用户看）：
      [MODULE: health|fatigue|emotion|companion]  模块推荐卡
      [DOC: 标题 | 要点1;要点2;要点3]              资料卡（右侧资料框展示）
    同时存在时资料卡优先。
    """
    doc = None
    module = None
    clean_lines = []
    for line in text.splitlines():
        dm = re.search(r"\[DOC:\s*([^|\]]+?)\s*\|\s*([^\]]+)\]", line)
        mm = re.search(r"\[MODULE:\s*(health|fatigue|emotion|companion)\s*\]", line)
        if dm:
            title = dm.group(1).strip()
            points = [x.strip() for x in re.split(r"[;；]", dm.group(2)) if x.strip()]
            if title and points:
                doc = {"kind": "doc", "title": title[:40], "points": points[:8]}
        elif mm:
            key = mm.group(1)
            module = {"kind": "module", "key": key, **MODULES[key]}
        else:
            clean_lines.append(line)
    return "\n".join(clean_lines).strip(), (doc or module)


@router.post("/companion/chat")
async def companion_chat(req: ChatRequest):
    reply_text = ""
    module = None
    try:
        client = _companion_client()
        completion = _chat_completion(
            client,
            model=AI_CONFIG["model"],
            messages=_build_messages(req),
            temperature=0.6,
            max_tokens=220,
        )
        reply_text, module = _parse_module(_extract_content(completion))
    except Exception as exc:  # 本地 LLM 未启动等情况：兜底
        logger.warning("[数字人] LLM 调用失败，走本地兜底: %s", exc)

    if not reply_text:
        # 兜底也要保留模块推进能力：关键词粗匹配
        reply_text = FALLBACK_REPLIES[hash(req.message) % len(FALLBACK_REPLIES)]
        for key, words in {
            "fatigue": ["困", "累", "疲劳", "睡", "乏"],
            "emotion": ["心情", "情绪", "难过", "焦虑", "生气", "烦"],
            "health": ["舌", "心率", "身体", "不舒服", "疼", "健康"],
        }.items():
            if any(w in req.message for w in words):
                module = {"key": key, **MODULES[key]}
                break

    return success({
        "reply": reply_text,
        "module": module,
        "greet": req.first_utterance,
    })
