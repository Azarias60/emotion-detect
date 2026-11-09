# -*-coding: utf-8 -*-
"""大模型分析服务：情绪报告生成、健康问诊分析、心理问题生成与疲劳建议。"""
import json
import re
from openai import OpenAI

from core.config import AI_CONFIG
from core.logger import get_logger

logger = get_logger(__name__)

# 心理问诊的本地兜底问题（大模型不可用或产出不足 5 题时使用）
DEFAULT_PSYCHOLOGY_QUESTIONS = [
    "1.最近心情怎么样？", "2.睡眠还好吗？", "3.压力大吗？", "4.有什么烦恼吗？", "5.饮食正常吗？",
    "6.工作顺利吗？", "7.社交多吗？", "8.运动吗？", "9.有爱好吗？", "10.对未来乐观吗？",
]


def extract_emotion_count_from_report(report: str) -> dict:
    pattern = r"喜怒哀虑统计：喜:(\d+) 怒:(\d+) 哀:(\d+) 虑:(\d+)"
    match = re.search(pattern, report)
    if match:
        return {
            "喜": int(match.group(1)),
            "怒": int(match.group(2)),
            "哀": int(match.group(3)),
            "虑": int(match.group(4)),
        }
    return {"喜": 0, "怒": 0, "哀": 0, "虑": 0}


def _get_client() -> OpenAI:
    api_key = AI_CONFIG.get("api_key") or "local"
    return OpenAI(base_url=AI_CONFIG["base_url"], api_key=api_key, timeout=60.0)


def _chat_completion(client: OpenAI, **kwargs):
    """通用聊天补全调用。
    针对本地推理/思考型模型（如 Ternary-Bonsai-2-27B），默认关闭冗长思维链以加速并避免耗尽 token；
    若服务端不支持 chat_template_kwargs 则自动无缝降级为普通调用。
    """
    try:
        kwargs_no_think = dict(kwargs)
        extra_body = dict(kwargs_no_think.get("extra_body") or {})
        extra_body.setdefault("chat_template_kwargs", {"enable_thinking": False})
        kwargs_no_think["extra_body"] = extra_body
        return client.chat.completions.create(**kwargs_no_think)
    except Exception:
        return client.chat.completions.create(**kwargs)


def _extract_content(completion) -> str:
    """提取大模型生成文本，若 content 为空且存在 reasoning_content 则容错提取。"""
    if not completion or not completion.choices:
        return ""
    message = completion.choices[0].message
    content = message.content or ""
    if not content.strip() and getattr(message, "reasoning_content", None):
        content = message.reasoning_content or ""
    return content.strip()


def generate_emotion_analysis_report(analysis_data: dict) -> str:
    """结合面部表情、语音声调、文本情绪与生理指标生成情绪诊断报告。"""
    try:
        client = _get_client()
        system_prompt = (
            "我需要你根据我给出的所有数据(结合我说的话)，分析出我的当前情绪如何,并给出分析报告。\n"
            "并在所有文字分析之后给出无标点符号的二字最终情绪，如：最终判定：开心、最终判定：生气、最终判定：平静。\n"
            "同时我不需要你给出指导建议，只需要分析数据；并且我希望你能详细分析，如：心率在什么时段突然升高或降低，表情突然变化巨大的原因等。将讲话的内容也要进行分析。\n"
            "特别要求：在文字分析下方，必须按照固定格式输出讲话内容中喜、怒、哀、虑四类情绪关键词的出现次数，格式为【喜怒哀虑统计：喜:X 怒:X 哀:X 虑:X】，X为数字，不要添加其他内容。"
        )
        user_content = f"请分析以下健康数据：\n\n{analysis_data}"
        completion = _chat_completion(
            client,
            model=AI_CONFIG["model"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.7,
            max_tokens=1800,
        )
        report = _extract_content(completion)
        return report or "分析报告生成为空\n最终判定：平静\n喜怒哀虑统计：喜:0 怒:0 哀:0 虑:0"
    except Exception as e:
        logger.error("AI生成失败: %s", e)
        return f"分析报告生成失败：{str(e)}\n最终判定：未知\n喜怒哀虑统计：喜:0 怒:0 哀:0 虑:0"


def generate_fatigue_advice(physiological: dict) -> dict:
    """根据 PERCLOS、眨眼与哈欠等生理指标生成疲劳等级与干预建议。"""
    probability = float(physiological.get("fatigue_probability", 0) or 0)
    if probability >= 0.75:
        level = "重度疲劳"
        advice = "请立即停止驾驶或高风险操作，离开屏幕并充分休息；若持续不适，请及时就医。"
    elif probability >= 0.5:
        level = "中度疲劳"
        advice = "建议暂停当前工作，闭目休息15至20分钟，并补充水分、适度活动。"
    elif probability >= 0.25:
        level = "轻度疲劳"
        advice = "建议短暂远眺和伸展，调整坐姿，并在下一工作周期安排休息。"
    else:
        level = "状态良好"
        advice = "当前未发现明显疲劳特征，请继续保持规律作息和适度休息。"
    return {"level": level, "text": advice}


def _tongue_question_fallback(tongue_data: dict) -> list:
    tongue = tongue_data.get("tongue", {}) if isinstance(tongue_data, dict) else {}
    coated = tongue.get("coated", {}).get("label", "")
    indentation = tongue.get("indentation", {}).get("label", "")
    questions = [
        f"检测到您的舌象为{coated or '当前结果'}，最近是否经常感觉口干、口黏或口中有异味？",
        "最近一周的睡眠质量和入睡情况怎么样？",
        "最近是否有胃口变化、腹胀或消化不适？",
        f"检测到{indentation or '舌体特征'}，近期是否容易感到身体乏力或精神不足？",
        "最近的情绪、压力和工作生活节奏有没有明显变化？",
    ]
    return [f"{index + 1}. {question}" for index, question in enumerate(questions)]


def generate_health_questions(tongue_data: dict) -> list:
    """结合舌象特征动态生成 5 个循序渐进的健康问诊问题。"""
    fallback = _tongue_question_fallback(tongue_data)
    try:
        client = _get_client()
        prompt = (
            "你是健康问诊助手。根据舌象检测结果提出5个循序渐进、适合口头回答的健康问诊问题。"
            "问题必须结合舌色、苔色、裂纹和齿痕，覆盖口腔感受、睡眠、消化、精力和情绪。"
            "只输出带1.到5.编号的问题，每行一个，不要添加解释。\n"
            f"舌象结果：{json.dumps(tongue_data, ensure_ascii=False)}"
        )
        completion = _chat_completion(
            client,
            model=AI_CONFIG["model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=700,
        )
        content = _extract_content(completion)
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        questions = [line for line in lines if re.match(r"^\d+[.、．]", line)]
        return questions[:5] if len(questions) >= 3 else fallback
    except Exception as error:
        logger.error("健康问诊问题生成失败，使用本地问题：%s", error)
        return fallback


def generate_health_analysis_report(
    tongue_data: dict,
    questions: list,
    answers: list,
    physiological: dict,
    heart_rate: dict,
    temperature: dict,
    *,
    voice_segments: list | None = None,
) -> str:
    """综合舌象、生理指标与用户问诊回答输出综合健康报告。"""
    payload = {
        "tongue": tongue_data,
        "questions": questions,
        "answers": answers,
        "voice_segments": voice_segments or [],
        "physiological": physiological,
        "heart_rate": heart_rate,
        "temperature": temperature,
    }
    fallback = (
        f"舌象参考：{tongue_data.get('report', {}).get('one_line', '已完成四维舌象检测')}。\n\n"
        f"平均心率：{heart_rate.get('avg_hr', 0)} bpm。\n\n"
        "问诊回答已纳入本次综合评估，建议结合睡眠、饮食和情绪变化持续观察。"
        "本报告为健康参考，不能替代专业医疗诊断。"
    )
    try:
        client = _get_client()
        completion = _chat_completion(
            client,
            model=AI_CONFIG["model"],
            messages=[
                {
                    "role": "system",
                    "content": "你是健康问诊报告助手。结合舌象、心率、生理指标、用户回答及硬件录音转写（voice_segments），不要臆造转写片段与单个问题的对应关系，输出结构清晰的健康参考报告。不要做疾病确诊，最后必须强调仅供参考。",
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.5,
            max_tokens=1800,
        )
        content = _extract_content(completion)
        return content or fallback
    except Exception as error:
        logger.error("健康综合报告生成失败，使用本地报告：%s", error)
        return fallback


def generate_psychology_questions() -> list:
    """生成或回退心理咨询建议问题。"""
    try:
        client = _get_client()
        completion = _chat_completion(
            client,
            model=AI_CONFIG["model"],
            messages=[
                {
                    "role": "system",
                    "content": "请根据常见的心理健康咨询场景，生成10个专业性的建议心理问题(这些问题需要是偏日常对话的问题用于人进行提问，而不是简单的是或不是的问题)。问题应涵盖不同方面的心理状态和情绪感受，保持专业性和适当的开放性。",
                },
                {"role": "user", "content": "请生成10个循序渐进专业性的建议心理问题用作提问参考。生成后的格式只需要1...,2...,3...这个样子不要有任何前后文"},
            ],
            temperature=0.7,
            max_tokens=800,
        )
        raw_result = _extract_content(completion)
        lines = raw_result.split("\n")
        generated_questions = []
        for line in lines:
            line = line.strip()
            if re.match(r"^\d+[\.、]", line):
                generated_questions.append(line)
                if len(generated_questions) == 10:
                    break
        return generated_questions if len(generated_questions) >= 5 else DEFAULT_PSYCHOLOGY_QUESTIONS
    except Exception as e:
        logger.error("AI生成问题失败，使用默认值: %s", e)
        return DEFAULT_PSYCHOLOGY_QUESTIONS
