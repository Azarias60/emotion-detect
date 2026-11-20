"""Emotion report model calls with explicit upstream failures.

The prompt is composed here, deliberately, from the fact inputs the analysis
flow mapped; absent capabilities stay explicit ``None`` values."""

from server.analysis.emotion.reporting.types import (
    EmotionReportFacts,
    EmotionReportText,
)
from server.analysis.llm_client import complete_text, request_errors
from server.analysis.prompt_policy import PromptSpec

EMOTION_REPORT_PROMPT = PromptSpec(
    role="你是情绪数据分析助手。",
    task="结合表情、行为、疲劳、问答、语音及生理测量数据生成详细的情绪分析报告。",
    constraints=(
        "仅分析提供的数据，不给出指导建议或干预方案。",
        "只有表情带时间序列证据时才描述变化；视频心率是整段估计，不推断突变时间或原因。",
        "answers 是逐题确认的问答；speech 是整段录音转写。重叠内容只计一次，不得自行将 speech 配给问题或编造回答。",
        "详细分析讲话内容；喜怒哀虑关键词次数仅根据讲话内容统计。",
    ),
    output=(
        "先输出详细的文字分析，说明有证据支持的情绪和测量观察。",
        "用 Markdown 组织正文；文字分析后单独输出二字最终情绪，格式为“最终判定：开心”（也可为生气、平静等）。",
        "另起一行输出“喜怒哀虑统计：喜:X 怒:X 哀:X 虑:X”，X 为整数，不添加其他标点。",
    ),
)


class OpenAIEmotionReportTextReader:
    def __init__(self, client_factory):
        self._client_factory = client_factory

    def result(self, facts: EmotionReportFacts) -> EmotionReportText:
        with request_errors("情绪报告"):
            client = self._client_factory()
            prompt_data = {
                "expression": facts.expression,
                "behavior": facts.behavior,
                "fatigue": facts.fatigue,
                "advice": facts.advice,
                "answers": [
                    {"question": answer.question, "answer": answer.answer}
                    for answer in facts.answers
                ],
                "speech": facts.speech,
                "physiology": facts.physiology,
            }
            text = complete_text(
                client,
                prompt=EMOTION_REPORT_PROMPT,
                input_data=prompt_data,
                empty_message="情绪报告服务返回空内容",
            )
            return EmotionReportText(text)
