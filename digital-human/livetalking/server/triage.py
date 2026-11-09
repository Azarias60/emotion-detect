# -*- coding: utf-8 -*-
"""导览页模块推荐分诊算法（2026-10-23）。

设计目标：判断什么时候该推荐 健康问诊 / 疲劳检测 / 情绪识别。
替代旧的"连续两句同一负向情绪"单一触发器——那条规则保留为其中一个
触发条件，但判定本身改为多维度并行打分：

  每句用户话 → 三个维度同时打分（关键词权重，带否定抑制）
             + 直接请求直通（"我想测XX"）
             + 深夜/凌晨疲劳先验（一次性）
  会话内累积（每句衰减 0.65，近期提及权重更高）
  触发规则（优先级从高到低）：
    R1 即时触发：单句某维度得分 ≥ 4（两个强信号词，如"熬夜又睡不着"）
    R2 过线触发：本句有该维度信号且会话累计 ≥ 1.5（轻度词第二句自然过线、
       深夜提"累"单句即过线——取代旧"连续两句"规则，累积本身涵盖连续性）
    R3 累积触发：某维度累计 ≥ 4.5（分散提及多次，如三句都带"累"）
  平级裁决：得分相同时 躯体(health) > 疲劳(fatigue) > 情绪(emotion)
            ——身体不适优先，是分诊的安全原则
  每个模块每次会话只推荐一次。

零依赖、确定性、可单测（见文件底部用例注释）。
"""
from __future__ import annotations

import re
from datetime import datetime

DIMENSIONS = ("health", "fatigue", "emotion")

# 关键词表：word -> 权重（2=强信号，1=普通信号）。
# 词表是纯规则资产：调词只改这里，不改算法。
HEALTH_WORDS: dict[str, int] = {
    # 强：明确躯体症状
    "头疼": 2, "头痛": 2, "头晕": 2, "恶心": 2, "胸闷": 2, "发烧": 2,
    "血压": 2, "血糖": 2, "拉肚子": 2, "肚子疼": 2, "胃疼": 2,
    # 普通：一般不适
    "不舒服": 1, "难受": 1, "疼": 1, "痛": 1, "咳嗽": 1, "嗓子疼": 1,
    "嗓子干": 1, "腰疼": 1, "脖子疼": 1, "眼睛干": 1, "眼睛涩": 1,
    "鼻塞": 1, "流鼻涕": 1, "便秘": 1, "溃疡": 1, "耳鸣": 1, "没胃口": 1,
}
FATIGUE_WORDS: dict[str, int] = {
    # 强：睡眠问题
    "熬夜": 2, "失眠": 2, "睡不着": 2, "睡不著": 2, "没睡好": 2, "多梦": 2,
    "睡不够": 2, "早醒": 2,
    # 普通：疲劳感受
    "累": 1, "疲惫": 1, "乏力": 1, "没劲": 1, "困": 1, "犯困": 1,
    "打哈欠": 1, "哈欠": 1, "没精神": 1, "倦怠": 1, "撑不住": 1, "眼睛酸": 1,
    "疲劳": 1, "加班": 1, "睡不好": 2, "没力气": 1, "睡不好觉": 2,
}
EMOTION_WORDS: dict[str, int] = {
    # 强：显著情绪困扰
    "焦虑": 2, "抑郁": 2, "崩溃": 2, "想哭": 2, "烦躁": 2, "生气": 2,
    "愤怒": 2, "害怕": 2, "压力": 2, "紧张": 2, "担心": 2,
    # 普通：一般情绪低落
    "难过": 1, "伤心": 1, "低落": 1, "郁闷": 1, "沮丧": 1, "孤独": 1,
    "委屈": 1, "不安": 1, "不开心": 1, "心慌": 1, "慌": 1, "烦": 1,
    "没意思": 1, "情绪": 1, "心情": 1,
}

WORD_TABLES = {"health": HEALTH_WORDS, "fatigue": FATIGUE_WORDS, "emotion": EMOTION_WORDS}

# 直接请求（"我想测一下疲劳"）：命中维度直通高分。
# 询问介绍（"疲劳检测是什么"）不算请求——抑制。
DIRECT_REQUEST_WORDS = ("测一下", "测一测", "检测", "检查一下", "查一下", "做一次", "做个")
INFO_QUESTION = re.compile(r"是什么|什么意思|怎么测|怎么用|介绍")
# 纯模块名词（为"想测疲劳"这类直呼请求加进词表的）：出现在信息问句里
# 只是提问对象（"疲劳检测是什么？"），不是症状/请求，不计分
MODULE_NAME_WORDS = {"疲劳", "情绪", "健康", "心情"}

# 模块点名直通（2026-10-24）：用户直接说出模块名（"我想做健康问诊"），
# 但"健康问诊/问诊/体检"不在 HEALTH_WORDS 词表里——得分 0，推荐卡永远
# 不切换（用户实测"说健康问诊推荐没变"）。点名=明确意图：非问句时直接
# 推荐该模块，且**不受"每模块一次"限制**（再次点名应刷新卡片）。
MODULE_TRIGGER = {
    "health": ("健康问诊", "问诊", "体检", "健康检测"),
    "fatigue": ("疲劳检测",),
    "emotion": ("情绪识别",),
}

MODULE_NAME = {"health": "健康问诊", "fatigue": "疲劳检测", "emotion": "情绪识别"}
MODULE_ADVICE = {
    "health": "先做一次健康问诊，看看身体的状态",
    "fatigue": "建议做一次疲劳检测，看看疲劳程度",
    "emotion": "建议做一次情绪识别，了解现在的情绪状态",
}

# 否定窗口：命中词前 3 个字符内的这些模式把该词判为否定（"不头疼""没怎么累"）。
# 注意只用 不/没——"别"会把"特别郁闷"误杀（实测踩过）。
_NEGATION = re.compile(r"[不没]$|[不没][太怎么那么]{0,2}$")

DECAY = 0.65          # 每句衰减：近因优先
IMMEDIATE = 4.0       # R1：单句直通
CONSECUTIVE = 1.0     # R2：过线门槛（1.0=任一信号词即推荐；曾 1.5 导致
                      # 白天单句轻度词不触发，用户反馈"推荐意愿少"后调低）
ACCUMULATE = 4.5      # R3：分散多次累积门槛
NIGHT_PRIOR = 1.0     # 深夜（23 点后 / 6 点前）疲劳先验，一次性


def _score_utterance(text: str) -> tuple[dict[str, float], dict[str, list[str]]]:
    """一句用户话 → (三维得分, 每维命中的词)。否定命中的词计 0 分但仍记录。

    同维度内长词优先：'胃疼'命中后，其内部再出现'疼'不重复计分
    （否则"胃疼得难受"会被数成 4 分，单句就误触发 R1）。
    """
    scores = {d: 0.0 for d in DIMENSIONS}
    hits: dict[str, list[str]] = {d: [] for d in DIMENSIONS}
    t = text or ""
    info_q = bool(INFO_QUESTION.search(t))
    for dim, table in WORD_TABLES.items():
        # 收集该维度全部命中（长词优先贪心去重叠）
        found = sorted(
            ((t.find(w), w, weight) for w, weight in table.items() if w in t),
            key=lambda x: (-len(x[1]), x[0]),
        )
        taken: list[tuple[int, int]] = []
        for pos, word, weight in found:
            if any(s <= pos < s + len(w_used) for s, w_used in taken):
                continue  # 已被更长的词覆盖
            taken.append((pos, word))
            if info_q and word in MODULE_NAME_WORDS:
                continue  # 信息问句里的模块名词不作为信号（"XX检测是什么"）
            prefix = t[max(0, pos - 3):pos]
            if prefix and _NEGATION.search(prefix):
                hits[dim].append(word)  # 记录但不计分（"不怎么累"不算疲劳信号）
            else:
                scores[dim] += weight
                hits[dim].append(word)
    # 直接请求加成：请求语 + 某维度词 → 该维度 +3（叠在词分上基本必触发）。
    # 纯咨询（"XX检测是什么"）不算请求，抑制掉。
    if any(w in t for w in DIRECT_REQUEST_WORDS) and not INFO_QUESTION.search(t):
        for dim in DIMENSIONS:
            if hits[dim]:
                scores[dim] += 3.0
    return scores, hits


class TriageScorer:
    """会话级分诊器：feed 每句用户话，返回推荐事件（无需推荐时 None）。"""

    def __init__(self, hour: int | None = None) -> None:
        self.scores = {d: 0.0 for d in DIMENSIONS}
        self.hits_recent: dict[str, list[str]] = {d: [] for d in DIMENSIONS}
        self.fired: set[str] = set()
        self._night_applied = False
        self._hour = hour if hour is not None else datetime.now().hour

    def _dominant(self) -> str:
        """当前累计最高的维度；平分时 health > fatigue > emotion（躯体优先）。"""
        return max(DIMENSIONS, key=lambda d: (self.scores[d], -DIMENSIONS.index(d)))

    def feed(self, text: str) -> dict | None:
        u, hits = _score_utterance(text)
        # 点名直通：非信息问句时说出模块名（"我想做健康问诊"）＝明确意图，
        # 直接推荐该模块；不受"每模块一次"限制（再次点名刷新卡片）。
        explicit = None
        if not INFO_QUESTION.search(text or ""):
            for dim, names in MODULE_TRIGGER.items():
                if any(n in (text or "") for n in names):
                    explicit = dim
                    break
        if explicit is not None:
            self.fired.discard(explicit)
            self.fired.add(explicit)
            return {
                "type": "recommend",
                "kind": "module",
                "module": explicit,
                "reason": f"{MODULE_ADVICE[explicit]}？",
                "rule": "点名直通",
            }
        # 衰减后累加本句
        for d in DIMENSIONS:
            self.scores[d] = self.scores[d] * DECAY + u[d]
        for d in DIMENSIONS:
            if hits[d]:
                self.hits_recent[d] = (self.hits_recent[d] + hits[d])[-4:]
        # 深夜先验（一次性）：23 点后/6 点前，疲劳基础分 +1
        if not self._night_applied and (self._hour >= 23 or self._hour < 6):
            self.scores["fatigue"] += NIGHT_PRIOR
            self._night_applied = True

        # 主导维度只在"未推荐过"的维度里选：已推荐维度的残留分
        # 不该压住新维度（曾导致 health 推荐后 fatigue 永远无法主导）
        dom = max(
            (d for d in DIMENSIONS if d not in self.fired),
            key=lambda d: (self.scores[d], -DIMENSIONS.index(d)),
            default=None,
        )

        fired_rule = None
        if dom is not None:
            if u[dom] >= IMMEDIATE:
                fired_rule = "R1 即时（单句强信号）"
            elif u[dom] >= 1.0 and self.scores[dom] >= CONSECUTIVE:
                # 本句有该维度信号且会话累计过线——单句轻度词第二句自然过线
                # （0.65+1=1.65），深夜先验则让单句"累"直接过线
                fired_rule = "R2 连续/累计过线"
            elif self.scores[dom] >= ACCUMULATE:
                fired_rule = "R3 累积（会话内多次提及）"
        if not fired_rule:
            return None

        self.fired.add(dom)
        words = [w for w in self.hits_recent[dom] if w in (text or "")] or self.hits_recent[dom]
        mention = f"你提到了{('、'.join(dict.fromkeys(words)))[:24]}，" if words else ""
        return {
            "type": "recommend",
            "kind": "module",
            "module": dom,
            "reason": f"{mention}{MODULE_ADVICE[dom]}？",
            "rule": fired_rule,
        }

    def reset(self) -> None:
        self.scores = {d: 0.0 for d in DIMENSIONS}
        self.hits_recent = {d: [] for d in DIMENSIONS}
        self.fired.clear()
        self._night_applied = False


# ── 判定用例（单测在 tests 侧，这里留行为速查）──
# "我最近头疼，还有点恶心"        → health 4  → R1 立即推荐健康问诊
# "天天熬夜，晚上总睡不着"        → fatigue 4 → R1 立即推荐疲劳检测
# "我好焦虑，压力特别大"          → emotion 4 → R1 立即推荐情绪识别
# "我最近好累" → "天天加班撑不住"  → R2 过线推荐疲劳检测
# "我很难过" → "还是很郁闷"       → R2 过线推荐情绪识别
# "有点烦" ×3（分散）             → R3 累积推荐情绪识别
# "我想测一下血压"                → 直接请求 → 健康问诊
# "压力大睡不着"（白天）          → fatigue 2 vs emotion 2 → 平分躯体优先不适用 →
#                                   health 0 在前不参与，平分次序 fatigue 先 → 疲劳检测
# "不怎么累，就是心情不好"        → 累被否定不计分 → 情绪链路
