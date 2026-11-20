"""检测各执行环节与报告、采集的对接：准备输入、指标产出与模型解读。

准备把望诊事实与题目交给报告；测量消费封存输入并把指标事实提供给
报告；模型处理只产出解读文字。这里的转换只做归属交接与既有事实投影，
不复制业务规则；报告内的舌象事实是健康测量的输入来源。
"""
import asyncio  # noqa: F401 - 检测流程适配层预留（当前转换均为纯同步投影）
from dataclasses import asdict, fields, is_dataclass
from typing import Mapping

from server.analysis.emotion.facts import expression_fact
from server.analysis.emotion.reporting.types import (
    EmotionAnswer, EmotionReportInput,
)
from server.analysis.facts import (
    behavior_observation_fact, fatigue_advice_fact, fatigue_assessment_fact,
    speech_fact,
)
from server.analysis.health.facts import (
    HealthAnswer, constitution_assessment_fact, constitution_report_fact,
    health_answer_fact, labeled_tongue_recognition, tongue_quality_fact,
)
from server.analysis.health.reporting.types import HealthReportInput
from server.compute.tongue.types import (
    ConstitutionAssessment, ConstitutionCandidate, ConstitutionReport,
    TongueDimensionResult, TongueQuality, TongueRecognition,
)
from server.concurrency import run_blocking
from server.detection_errors import DetectionInputError


def _payload(value):
    """把事实对象转成 JSON 安全载荷；只做结构转换，不改字段语义。"""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _payload(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_payload(item) for item in value]
    raise TypeError(f"cannot serialize {type(value).__name__} to report facts")


def tongue_facts(tongue):
    """望诊结果的报告事实；与既有展示契约同形（识别项带中文标签）。"""
    return {
        "quality": tongue_quality_fact(tongue.quality),
        "recognition": labeled_tongue_recognition(tongue.recognition),
        "constitution": constitution_assessment_fact(tongue.constitution),
        "constitution_report": constitution_report_fact(tongue.constitution_report),
    }


def _tongue_input(result):
    """从报告中的望诊事实恢复健康测量输入；缺失项保持缺失。"""
    facts = result.get("tongue") or {}
    quality = facts.get("quality")
    recognition = facts.get("recognition")
    constitution = facts.get("constitution")
    report = facts.get("constitution_report")
    return (
        TongueQuality(**quality) if quality else None,
        TongueRecognition(**{
            key: TongueDimensionResult(value["class_name"], value["probability"])
            for key, value in recognition.items()
        }) if recognition else None,
        ConstitutionAssessment(candidates=tuple(
            ConstitutionCandidate(**item) for item in constitution["candidates"]
        )) if constitution else None,
        ConstitutionReport(**report) if report else None,
    )


def create_prepare(reports, tongue_observation, emotion_questions):
    """模式准备：健康望诊写入舌象事实并返回题目；情绪返回题目。"""

    async def prepare(owner, report_id, kind):
        if kind == "health":
            observed = await tongue_observation.observe_tongue()
            reports.record_facts(report_id, {"tongue": tongue_facts(observed.tongue)})
            return tuple(observed.questions.questions)
        if kind == "emotion":
            questions = await run_blocking(emotion_questions.result)
            return tuple(questions.questions)
        raise DetectionInputError(f"{kind} 模式没有准备环节")

    return prepare


def fatigue_facts(measured, detection_id):
    """疲劳指标的报告事实；建议随指标一次提供，无独立模型解读。"""
    return {
        "detection_id": str(detection_id),
        "behavior": behavior_observation_fact(measured.behavior),
        "fatigue": (
            {**fatigue_assessment_fact(measured.fatigue),
             "score_fraction": measured.fatigue.score_fraction}
            if measured.fatigue is not None else None
        ),
        "advice": fatigue_advice_fact(measured.advice),
        "physiology": _payload(measured.physiology),
        "measurement_notice": measured.measurement_notice,
        "physiology_diagnostics": asdict(measured.diagnostics) if measured.diagnostics else None,
    }


def create_measure(reports, admission, fatigue_flow, emotion_flow, health_flow):
    """指标测量：消费封存输入与已结清问答，把指标事实提供给报告。"""

    def _common_facts(measured):
        return {
            "behavior": behavior_observation_fact(measured.behavior),
            "fatigue": (
                {**fatigue_assessment_fact(measured.fatigue),
                 "score_fraction": measured.fatigue.score_fraction}
                if measured.fatigue is not None else None
            ),
            "advice": fatigue_advice_fact(measured.advice),
            "speech": speech_fact(measured.speech),
            "physiology": _payload(measured.physiology),
            "physiology_diagnostics": asdict(measured.diagnostics) if measured.diagnostics else None,
            "detection_id": str(measured.detection_id),
        }

    async def measure(report_id, sealed, answers):
        kind = reports.report_facts(report_id)["type"]
        if kind == "fatigue":
            with admission.acquire("fatigue", "疲劳检测任务正在分析，请勿重复提交"):
                measured = await fatigue_flow.run(sealed)
            return {"facts": fatigue_facts(measured, report_id), "input": None}
        if kind == "emotion":
            input = EmotionReportInput(
                detection_id=str(report_id),
                answers=tuple(EmotionAnswer(question=q, answer=a) for q, a in answers),
            )
            with admission.acquire("emotion", "情绪检测任务正在分析，请勿重复提交"):
                measured = await emotion_flow.measure(input, sealed)
            facts = _common_facts(measured)
            facts["expression"] = (
                _payload(expression_fact(measured.expression))
                if measured.expression is not None else None
            )
            return {"facts": facts, "input": measured}
        tongue_quality, tongue_recognition, constitution, constitution_report = _tongue_input(
            reports.report_facts(report_id)["result"])
        input = HealthReportInput(
            tongue_quality=tongue_quality, tongue_recognition=tongue_recognition,
            constitution=constitution, constitution_report=constitution_report,
            answers=tuple(HealthAnswer(question=q, answer=a) for q, a in answers),
            detection_id=str(report_id),
        )
        with admission.acquire("health", "健康问诊任务正在分析，请勿重复提交"):
            measured = await health_flow.measure(input, sealed)
        facts = _common_facts(measured)
        facts["answers"] = [health_answer_fact(answer) for answer in measured.answers]
        # 舌象事实归准备环节一次性提供，指标事实不重复携带第二份。
        return {"facts": facts, "input": measured}

    return measure


def create_analyze(reports, emotion_flow, health_flow):
    """模型解读：健康与情绪产出综合解读；疲劳的建议已随指标提供。"""

    async def analyze(report_id, measured):
        kind = reports.report_facts(report_id)["type"]
        if kind == "fatigue":
            return {}
        if kind == "emotion":
            result = await emotion_flow.write(measured)
            return {"report": {"text": result.report.text}, "emotion_word_count": dict(result.word_count)}
        result = await health_flow.write(measured)
        return {"report": {"text": result.report.text}}

    return analyze


__all__ = ["create_analyze", "create_measure", "create_prepare", "tongue_facts"]
