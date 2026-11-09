# -*- coding: utf-8 -*-
"""对话情绪跟踪：逐句识别用户情绪，检测变化与连续同情绪，触发模块推荐。

为什么放在引擎侧而不是调用 8081 的人脸/语音情绪模型：
  对话链路是 麦克风 → WS → Whisper → LLM → TTS，每句都走一趟跨服务 HTTP
  会显著拖慢首字延迟；而这里要的粒度是"这句话偏什么情绪"（用于对话策略），
  不是"情绪占比报告"。因此用确定性的中文词表规则，零依赖、零延迟、结果稳定，
  且能解释（命中哪些词）。

对外事件（由 MicrophoneConnection 转成 WS json 推给前端）：
  emotion   {"type":"emotion","emotion":"anxious","label":"焦虑","changed":true,"consecutive":1}
  recommend（陪伴页 role=companion，2026-10-23 用户拍板：只做情绪检测，
            情绪不好时邀请看舞蹈/AI 短剧，素材由用户后续放入前端）
            {"type":"recommend","kind":"dance","reason":"感觉你有些低落，要不要看段舞放松一下？"}
  recommend（导览页 role=guide，2026-10-23：模块推荐归导览页）
            {"type":"recommend","kind":"module","module":"fatigue","reason":"听起来你有些疲惫…"}
"""
from __future__ import annotations

import re

from server.triage import TriageScorer

# 情绪 → (中文标签, 命中的关键词)。顺序即优先级：先判强烈负向。
EMOTIONS = [
    ("angry", "烦躁愤怒", ["生气", "愤怒", "气死", "恼火", "讨厌", "烦躁", "火大", "忍不了", "烦死"]),
    ("anxious", "焦虑紧张", ["焦虑", "紧张", "担心", "害怕", "不安", "压力", "慌", "心慌", "睡不着", "失眠", "怕"]),
    ("low", "低落疲惫", ["难过", "伤心", "低落", "郁闷", "沮丧", "孤独", "想哭", "委屈", "没意思",
                      "累", "疲惫", "乏力", "没劲", "崩溃", "撑不住", "压抑"]),
    ("somatic", "身体不适", ["头疼", "头痛", "头晕", "恶心", "胸闷", "心口", "胃", "疼", "不舒服",
                          "发烧", "咳嗽", "腰", "脖子", "眼睛干"]),
    ("positive", "心情不错", ["开心", "高兴", "喜欢", "舒服", "好多了", "愉快", "谢谢", "不错",
                          "挺好", "放松"]),
]

NEGATIVE = {"angry", "anxious", "low", "somatic"}

# 负向情绪 → 跳舞邀请（2026-10-23 用户拍板：情感数字人不做模块推荐，
# 只做情绪检测；情绪不好时邀请看一段舞蹈/AI 短剧——视频素材由用户
# 后续放入前端 public/theater/ 目录，这里只发邀请事件）
NEGATIVE_HINT = {
    "angry": "听起来你有些烦躁",
    "anxious": "感觉你有些焦虑",
    "low": "感觉你有些低落",
    "somatic": "你提到身体不太舒服",
}
FATIGUE_WORDS = ["累", "疲惫", "乏力", "没劲", "困", "熬夜", "睡不好", "撑不住"]

# 情绪变化的提示语（由前端可选播报，这里只给文字）
CHANGE_HINT = {
    "positive": "感觉你心情好了一些",
    "low": "听起来你有点低落",
    "anxious": "感觉你有些焦虑",
    "angry": "听起来你有些烦躁",
    "somatic": "你提到身体不太舒服",
    "neutral": "",
}


# 强焦虑词：出现时优先判焦虑，不被疲惫词覆盖
STRONG_ANXIOUS = ["焦虑", "紧张", "担心", "害怕", "不安", "压力", "心慌", "慌"]
# 否定式降级：不开心/没那么难过 → 仍属低落，但"不怎么难过"应回中性
NEGATED = re.compile(r"[不没]\s*[太怎么那么]{0,3}\s*")


def classify(text: str) -> tuple[str, str]:
    """返回 (emotion_key, label)；无命中为 neutral。"""
    t = (text or "").strip()
    if not t:
        return "neutral", "平静"
    # 疲惫词优先（"累/睡不着/熬夜"这类要推荐疲劳检测，不能被归到焦虑）
    if any(w in t for w in FATIGUE_WORDS) and not any(w in t for w in STRONG_ANXIOUS):
        return "low", "低落疲惫"
    for key, label, words in EMOTIONS:
        for w in words:
            if w in t:
                # "不开心/没那么难过" 是低落；"不怎么难过" 接近中性 → 让词表其余项决定
                if key == "positive" and NEGATED.search(t):
                    return "low", "低落疲惫"
                return key, label
    return "neutral", "平静"


class EmotionTracker:
    """逐句累计：变化检测 + 连续同情绪计数 + 推荐触发（每类只推一次）。

    role决定连续负向情绪时推荐什么：
      guide     导览页（检测主页）→ 推荐对应检测模块
      companion 陪伴页（情感数字人）→ 邀请看一段舞蹈
    """

    def __init__(self, role: str = "companion") -> None:
        self.current = "neutral"
        self.consecutive = 0
        self.recommended: set[str] = set()
        self.role = role
        # 导览页模块推荐走分诊算法（server/triage.py：三维打分+累积+三条
        # 触发规则），不再用"情绪类别→模块"硬映射
        self.triage = TriageScorer() if role == "guide" else None

    def _recommend(self, key: str, text: str) -> dict:
        return {
            "type": "recommend",
            "kind": "dance",
            "reason": f"{NEGATIVE_HINT.get(key, '感觉你状态不太好')}，要不要看段舞放松一下？",
            "emotion": key,
            "consecutive": self.consecutive,
        }

    def feed(self, text: str) -> list[dict]:
        """吃一句用户原话，返回需要推给前端的事件列表（0~2 个）。"""
        key, label = classify(text)
        events: list[dict] = []
        changed = key != self.current
        if changed:
            self.consecutive = 1
            self.current = key
        else:
            self.consecutive += 1
        events.append({
            "type": "emotion",
            "emotion": key,
            "label": label,
            "changed": changed,
            "consecutive": self.consecutive,
            "hint": CHANGE_HINT.get(key, "") if changed else "",
        })
        if self.role == "guide":
            # 分诊算法决定推荐哪个模块（含触发规则与冷却，每模块一次）
            rec = self.triage.feed(text)
            if rec is not None:
                rec["emotion"] = key
                events.append(rec)
        elif key in NEGATIVE and self.consecutive >= 2 and key not in self.recommended:
            # 陪伴页：连续两句同一负向情绪 → 邀请看舞蹈（每类一次）
            self.recommended.add(key)
            events.append(self._recommend(key, text))
        return events

    def reset(self) -> None:
        self.current = "neutral"
        self.consecutive = 0
        self.recommended.clear()
        if self.triage is not None:
            self.triage.reset()
