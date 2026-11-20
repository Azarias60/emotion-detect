"""Health report generation: prompt payload, OpenAI text reader and the
report text port. The tongue disclaimer stays with
the capability; composition binds the reader in ``assembly.py``."""

from collections.abc import Mapping

from server.analysis.health.reporting.types import (
    HealthReportFacts,
    HealthReportText,
)
from server.analysis.health.facts import health_answer_fact
from server.analysis.llm_client import complete_text, request_errors
from server.analysis.prompt_policy import PromptSpec

TONGUE_DISCLAIMER = "舌象结果仅供健康参考，不能替代专业医疗诊断。"


HEALTH_REPORT_PROMPT = PromptSpec(
    role="你是健康数据分析助手。",
    task="结合舌象质量、舌象识别、体质评估、行为、疲劳、建议、问答、语音和生理测量数据，生成结构清晰的健康参考报告。",
    constraints=(
        "answers 是逐题确认的问答；speech 是整段录音转写，没有可靠的逐题对应关系，不得自行配给问题。",
        "answers 与 speech 重复的内容只计一次，不得编造回答。",
        "不得做疾病确诊；缺失的信息只作为数据不足处理。",
    ),
    output=(
        "用 Markdown 小标题与列表组织健康参考报告，区分观测事实与参考分析。",
        "报告结尾必须强调仅供参考、不能替代专业医疗诊断。",
    ),
)


class OpenAIHealthReportTextReader:
    def __init__(self, client_factory):
        self._client_factory = client_factory

    def generate(self, payload: Mapping[str, object]):
        client = self._client_factory()
        return complete_text(
            client,
            prompt=HEALTH_REPORT_PROMPT,
            input_data=dict(payload),
            empty_message="健康报告服务返回空内容",
        )


class HealthReportTextPort:
    """Generate health report text over the shared LLM transport."""

    def __init__(self, text_reader):
        self._text_reader = text_reader

    def result(self, facts: HealthReportFacts) -> HealthReportText:
        payload: Mapping[str, object] = {
            "tongue_quality": facts.tongue_quality,
            "tongue_recognition": facts.tongue_recognition,
            "constitution": facts.constitution,
            "constitution_report": facts.constitution_report,
            "tongue_disclaimer": TONGUE_DISCLAIMER,
            "answers": [health_answer_fact(answer) for answer in facts.answers],
            "behavior": facts.behavior,
            "fatigue": facts.fatigue,
            "advice": facts.advice,
            "speech": facts.speech,
            "physiology": facts.physiology,
        }
        with request_errors("健康报告"):
            return HealthReportText(self._text_reader.generate(payload))
