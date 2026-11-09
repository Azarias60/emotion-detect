"""Low-latency browser microphone conversation over one WebSocket."""

from __future__ import annotations

import asyncio
import json
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import numpy as np
from aiohttp import WSMsgType, web

from llm import llm_response
from server.personas import active_system_prompt, get_persona
from server.session_manager import session_manager
from server.emotion_tracker import EmotionTracker

# 刷新口令（用户拍板 2026-10-23）：说完即清空会话——记忆、情绪、推荐全部归零
REFRESH_PHRASES = ("重新开始", "刷新一下", "重新来吧", "换个话题", "清除记忆", "清空一下")
# 导览页推荐的肯定确认词（说完「好/可以」即跳转对应模块）
ACCEPT_PHRASES = ("好的", "可以", "行", "去", "去吧", "测一下", "现在去", "好吧", "要", "好嘞", "走吧", "马上")

# 语音/文字**直接导航**的模块名表（2026-10-24 用户实测"跳转情绪识别失败"：
# 只有先推荐再确认一条路径，直接说"跳转到X"无效）。含陪伴页——用户试过
# "跳转到情感数字人页面"，前端 MODULE_PATH 需有 companion 才落得住。
JUMP_TARGETS = {
    "health": ("健康问诊", "问诊", "体检", "健康检测"),
    "fatigue": ("疲劳检测",),
    "emotion": ("情绪识别",),
    "companion": ("情感数字人", "陪伴", "数字人"),
}

# LLM 文本里主动推荐模块时（不经过情绪跟踪器），推荐卡需要的展示文案：
# 与语音内容同步（2026-10-26 用户反馈"推荐和资料卡不同步"——语音里推荐了，
# 卡片没动）。reason 与导览页预置文案同风格。
LLM_REC_REASONS = {
    "health": "暖暖建议先做一次健康问诊，了解身体基础状态。",
    "fatigue": "暖暖建议做一次疲劳检测，看看当前的疲劳程度。",
    "emotion": "暖暖建议做一次情绪识别，了解你现在的心情。",
    "companion": "想找人说说话的时候，情感数字人陪你聊聊天。",
}
from services.action_parser import ActionTagParser
from services.doc_parser import DOC_PROMPT_RULES, DocTagStripper
from services.llm_service import LLMService
from services.llm_memory import ConversationMemory
from services.text_slicer import SegmentSlicer
from services.wake_word import WakeWordGate, is_bare_greeting
from state_machine import init_asr
from utils.logger import logger


PCM_SAMPLE_RATE = 16000
PCM_BLOCK_SAMPLES = 512
SENTENCE_ENDINGS = set("，。！？；,.!?;")

# ── 常听仲裁（2026-10-24）──────────────────────────────────────────────
# 同一时刻只允许一个 default 模式连接真正收音：两个标签页同开时会各自开麦
# （同一物理麦克风）、各自触发独立 LLM+TTS 回复，用户听到"两重语音"
# （实测 16:01:45 同一句"你好"双唤醒双回复）。规则：**最新 configure 的
# 连接为持有者**——新页面打开/刷新即接管，旧页面收 mic_taken 后关麦并提示；
# 引擎重启后双方重连，最后完成 configure 的胜出（确定性、零抖动）。
_MIC_OWNER: "MicrophoneConnection | None" = None


def _mic_owner_alive() -> bool:
    return _MIC_OWNER is not None and not _MIC_OWNER.closed.is_set()

# 切句阈值：首段尽早送 TTS 压首音延迟，后续段维持较大段落保证韵律。
# 首段下限 6：zero-shot 续写模式下段落过短（相对音色注册文本）会让
# LLM 停不下来产生幻觉续说，首段必须 ≥ 注册文本（9 字）的一半左右。
FIRST_SEGMENT_MIN = 6
# 12→20（2026-10-24 用户拍板"第一句话可以长一些"）：首句与 greet 硬切同时起播，
# 稍长的首句让声音盖满 greet+greet2talk 两段说话动画（~5.7s），减少张嘴没声窗
# 20→12（2026-10-25 用户反馈"第一句话慢了"）：首句 12 字起步——TTS 首包固定
# ~1s（zero-shot 读 42 字样本），首句越长用户等得越久；12 字约 2.5s 语音，
# 首音体感明显提前，后续段落（SEGMENT_MIN=12）不受影响。
FIRST_SEGMENT_MAX = 12
# 首段快速开口，后续 12 字起步：太短会每句重付首包开销，太长（20+）
# 会拖慢第二句开口，攒段等待还会把硬上限切到词中间（"伙伴"被劈开）。
SEGMENT_MIN = 12
SEGMENT_MAX = 36

# Ghost-turn guard (borrowed from VoxEMW): the avatar's own audio can leak
# back into the microphone and get transcribed, creating a phantom turn where
# the assistant answers itself. A transcript that nearly duplicates the tail
# of a reply that is playing / just finished is dropped silently.
GHOST_WINDOW_S = 6.0
# 实时口型模式：正文交给 TTS 后还要排队合成（zzf2 这类训练音色每段
# 1.4~3.4s）再经 Wav2Lip 管线出声，最后一个字可能在"交段"十几秒后才播完。
# 窗口太短会让回声转写在窗口外溜过去，于是数字人自问自答、一直说。
GHOST_WINDOW_LIVE_S = 20.0
GHOST_MIN_CHARS = 8
GHOST_RECALL = 0.9


def is_ghost_transcript(
    transcript: str,
    reply_text: str | None,
    seconds_since_audio: float | None,
    window: float = GHOST_WINDOW_S,
) -> bool:
    t = (transcript or "").replace(" ", "")
    r = (reply_text or "").replace(" ", "")
    if len(t) < GHOST_MIN_CHARS or not r:
        return False
    if seconds_since_audio is None or seconds_since_audio > window:
        return False
    # 回声可能来自回复的任何位置（不只是结尾）：整体比对 + 尾部比对取最大
    candidates = [r[-120:]]
    if len(r) > 120:
        candidates.append(r)
    best = 0.0
    for candidate in candidates:
        matcher = SequenceMatcher(None, t, candidate)
        matched = sum(block.size for block in matcher.get_matching_blocks())
        # Echo = the transcript is almost entirely covered by the reply it
        # came from; novel user speech has low recall against the reply.
        best = max(best, matched / len(t))
    return best >= GHOST_RECALL


# Whisper 幻听黑名单：对噪声/近静音输入，Whisper 会从训练数据里的字幕
# 水印硬编出这些固定句式（实测白噪声会转写出"字幕by索兰娅"）。
HALLUCINATION_PATTERNS = (
    "字幕by", "字幕由", "字幕制作", "字幕提供", "听译", "时间轴",
    "谢谢观看", "感谢观看", "请不吝点赞", "点赞订阅", "订阅频道",
    "下期再见", "下集再见", "本期节目",
    "thanks for watching", "thank you for watching", "subtitles by",
    "amara.org",
)


def drop_hallucination(text: str) -> str:
    """Return '' when a transcript is a known Whisper watermark hallucination."""
    lowered = (text or "").lower()
    if any(pattern in lowered for pattern in HALLUCINATION_PATTERNS):
        return ""
    return text


class ResidentFunASR:
    """对话 ASR 的 FunASR 后端：HTTP 调 8081 常驻的 SenseVoiceSmall。

    不在引擎进程内加载 funasr——装进引擎 venv 会与其 torch/numpy 冲突
    （导入即段错误）。8081 的模型随后端生命周期常驻，本地 HTTP 往返
    <10ms，总延迟仍远低于本地 whisper。
    """

    def __init__(self, endpoint: str = "http://127.0.0.1:8081/asr/transcribe") -> None:
        self.endpoint = endpoint
        self._ok = True  # 连续失败标记：True 时尝试，失败 3 次后放弃
        self._fails = 0

    def warmup(self) -> None:
        pass  # 模型在 8081 侧，随其后端常驻；引擎侧无需预热

    def transcribe_pcm(self, pcm: np.ndarray) -> str:
        import io as _io
        import wave as _wave

        if not self._ok:
            return ""
        buf = _io.BytesIO()
        with _wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(PCM_SAMPLE_RATE)
            w.writeframes(pcm.astype("<i2").tobytes())
        try:
            import requests
            resp = requests.post(
                self.endpoint,
                files={"file": ("audio.wav", buf.getvalue(), "audio/wav")},
                timeout=8,
            )
            resp.raise_for_status()
            text = resp.json().get("text", "")
            self._fails = 0
            return drop_hallucination(text)
        except Exception as exc:
            self._fails += 1
            logger.warning("[Mic ASR] FunASR 后端失败(%d 次): %s", self._fails, exc)
            if self._fails >= 3:
                self._ok = False
                logger.error("[Mic ASR] FunASR 连续失败，放弃（应由上层回退 whisper）")
            return ""


class ResidentWhisper:
    """Load and warm Whisper once, then serialize inference on that instance."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None
        self._lock = threading.RLock()

    def _ensure_model(self):
        with self._lock:
            if self._model is None:
                logger.info("[Mic ASR] loading resident Whisper %s", self.model_name)
                self._model = init_asr(self.model_name)
                # Pay CUDA/kernel initialization before the first real turn.
                self._model.transcribe(
                    np.zeros(PCM_SAMPLE_RATE, dtype=np.float32),
                    language="zh",
                    fp16=self._uses_cuda(),
                )
                logger.info("[Mic ASR] resident Whisper is ready")
            return self._model

    @staticmethod
    def _uses_cuda() -> bool:
        try:
            import torch

            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def warmup(self) -> None:
        self._ensure_model()

    def transcribe_pcm(self, pcm: np.ndarray) -> str:
        audio = np.asarray(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            model = self._ensure_model()
            result = model.transcribe(
                audio,
                language="zh",
                fp16=self._uses_cuda(),
                # 不沿用上一段的上下文，避免幻听跨段传染
                condition_on_previous_text=False,
            )
        segments = result.get("segments") or []
        if segments:
            # 段级置信度过滤：静音段与低置信段是幻听的主要来源
            kept = [
                seg.get("text", "")
                for seg in segments
                if seg.get("no_speech_prob", 0.0) < 0.5
                and seg.get("avg_logprob", -9.0) > -1.0
            ]
            text = "".join(kept).strip()
        else:
            text = str(result.get("text", "")).strip()
        filtered = drop_hallucination(text)
        if filtered != text:
            logger.warning("[Mic ASR] 已过滤 Whisper 幻听: %r", text)
        return filtered


@dataclass
class VADEvent:
    kind: str
    audio: np.ndarray | None = None


class StreamingEnergyVAD:
    """Small server-side turn detector for already denoised browser PCM."""

    def __init__(
        self,
        *,
        threshold: float = 0.018,
        start_ms: int = 120,
        silence_ms: int = 600,
        max_utterance_s: float = 20.0,
        pre_roll_ms: int = 250,
    ) -> None:
        self.threshold = float(threshold)
        self.start_samples = int(PCM_SAMPLE_RATE * start_ms / 1000)
        self.silence_samples_limit = int(PCM_SAMPLE_RATE * silence_ms / 1000)
        self.max_samples = int(PCM_SAMPLE_RATE * max_utterance_s)
        self.pre_roll_limit = int(PCM_SAMPLE_RATE * pre_roll_ms / 1000)
        self.reset()

    def reset(self) -> None:
        self.speaking = False
        self._voice_samples = 0
        self._silence_samples = 0
        self._pre_roll: deque[np.ndarray] = deque()
        self._pre_roll_samples = 0
        self._speech: list[np.ndarray] = []
        self._speech_samples = 0

    @staticmethod
    def _rms(chunk: np.ndarray) -> float:
        values = chunk.astype(np.float32) / 32768.0
        return float(np.sqrt(np.mean(values * values))) if values.size else 0.0

    def _append_pre_roll(self, chunk: np.ndarray) -> None:
        self._pre_roll.append(chunk)
        self._pre_roll_samples += len(chunk)
        while self._pre_roll and self._pre_roll_samples > self.pre_roll_limit:
            removed = self._pre_roll.popleft()
            self._pre_roll_samples -= len(removed)

    def feed(self, pcm_bytes: bytes) -> list[VADEvent]:
        usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
        if usable <= 0:
            return []
        chunk = np.frombuffer(pcm_bytes[:usable], dtype="<i2").copy()
        voiced = self._rms(chunk) >= self.threshold
        events: list[VADEvent] = []

        if not self.speaking:
            self._append_pre_roll(chunk)
            self._voice_samples = self._voice_samples + len(chunk) if voiced else 0
            if self._voice_samples >= self.start_samples:
                self.speaking = True
                self._speech = list(self._pre_roll)
                self._speech_samples = sum(len(item) for item in self._speech)
                self._pre_roll.clear()
                self._pre_roll_samples = 0
                self._silence_samples = 0
                events.append(VADEvent("speech_started"))
            return events

        self._speech.append(chunk)
        self._speech_samples += len(chunk)
        self._silence_samples = self._silence_samples + len(chunk) if not voiced else 0
        if (
            self._silence_samples >= self.silence_samples_limit
            or self._speech_samples >= self.max_samples
        ):
            audio = np.concatenate(self._speech) if self._speech else np.empty(0, np.int16)
            self.reset()
            events.append(VADEvent("speech_stopped", audio))
        return events


def _windows_fallback_tts():
    """Offline Windows voice for segments the online TTS keeps failing."""
    from tts.windows_sapi import WindowsTTSVoice

    return WindowsTTSVoice()


class MicrophoneConnection:
    def __init__(self, ws: web.WebSocketResponse, opt, asr: ResidentWhisper) -> None:
        self.ws = ws
        self.opt = opt
        self.asr = asr
        self.mode = "default"
        self.session_id = ""
        self.generation = 0
        # 实时口型模式的回复令牌：live 的段落是直接灌进会话 TTS 队列的，
        # 不经过 WebSocket 代际过滤，必须用独立令牌让被取代的
        # llm_response 立刻停止供段，否则新旧两轮回复的语音会叠着播。
        self.live_reply_id = 0
        # 会话级多轮记忆：轻量页也让它记得住前面聊过什么。每个连接一份。
        self.llm_memory = ConversationMemory()
        # 对话情绪跟踪：逐句分类 + 连续同情绪 → 触发推荐。
        # role 由 configure 决定：guide（导览页）推检测模块，
        # companion（陪伴页）邀请看舞蹈（2026-10-23 用户分工拍板）
        self.emotions = EmotionTracker(role="companion")
        # 连接级人设覆盖（导览页 configure 带 persona=guide；空=用全局 live 人设）
        self.persona_override = ""
        # 给 LLM 的情绪上下文（一条短句，无情绪时为空）
        self._emotion_note = ""
        # 最近一帧 PCM 到达时刻（断流自愈用）
        self._last_pcm_at: float | None = None
        # 导览页刚发出的模块推荐（等用户一句「好/可以」确认跳转）
        self._last_rec: dict | None = None
        self.closed = threading.Event()
        self.loop = asyncio.get_running_loop()
        # A generation tag lets the sender discard audio that was queued before
        # the user interrupted the current reply.
        self.output: asyncio.Queue[tuple[str, Any, int | None]] = asyncio.Queue(
            maxsize=160
        )
        self.vad = StreamingEnergyVAD(
            threshold=getattr(opt, "mic_vad_threshold", 0.018),
            start_ms=getattr(opt, "mic_vad_start_ms", 120),
            silence_ms=getattr(opt, "mic_vad_silence_ms", 600),
            max_utterance_s=getattr(opt, "mic_max_utterance_s", 20.0),
        )
        self._tasks: set[asyncio.Task] = set()
        # Reopen grace state: a turn finished by VAD waits briefly before the
        # reply is dispatched. If the user starts speaking again inside the
        # window, the pending turn is folded back into the same utterance
        # instead of starting a reply that would instantly be interrupted.
        self.pending_turn: dict | None = None
        self.pending_task: asyncio.Task | None = None
        self.carry_audio: np.ndarray | None = None
        # Ghost-turn tracking: what the assistant is saying / just said.
        self.reply_text = ""
        self.last_reply_audio_at: float | None = None
        # Wake-word gate: microphone turns are dropped until the user says
        # the mode's word (暖暖 on the default page, 你好 in live mode).
        self.wake = WakeWordGate(
            getattr(opt, "wake_word_default", ""),
            getattr(opt, "wake_word_live", ""),
        )

    def _drop_pending_turn(self) -> None:
        self.pending_turn = None
        if self.pending_task is not None:
            self.pending_task.cancel()
            self.pending_task = None
        self.carry_audio = None

    async def _sender(self) -> None:
        while True:
            kind, payload, generation = await self.output.get()
            if generation is not None and generation != self.generation:
                continue
            if kind == "binary":
                await self.ws.send_bytes(payload)
            else:
                await self.ws.send_json(payload)

    async def _emit(self, payload: dict) -> None:
        await self.output.put(("json", payload, None))

    def _emit_from_thread(
        self,
        kind: str,
        payload: Any,
        generation: int,
    ) -> bool:
        if self.closed.is_set():
            return False
        future = asyncio.run_coroutine_threadsafe(
            self.output.put((kind, payload, generation)), self.loop
        )
        try:
            future.result(timeout=10)
            return True
        except Exception:
            return False

    def _live_action_emitter(self, generation: int):
        """Forward semantic-action tags fired in live mode to the live UI."""
        def on_action(tag: str) -> None:
            self._emit_from_thread(
                "json",
                {"type": "action", "action": tag, "mode": "live"},
                generation,
            )

        return on_action

    async def _try_confirm_jump(self, text: str) -> bool:
        """语音/文字确认模块推荐 → 发 module_jump 并重置会话。

        _last_rec 存活 3 轮或 45 秒：用户问句插在"推荐→确认"之间不该把
        待确认状态清掉（曾一问一答就置 None，导致确认词到达时无推荐可跳）。
        """
        if self.persona_override != "guide" or not self._last_rec:
            return False
        rec = self._last_rec
        age = time.monotonic() - rec.get("at", 0.0)
        # 4 轮/180s：实测节奏「你好→暖暖打招呼→寒暄一句→好」要吃掉 3 轮，
        # 2 轮/120s 时确认词到达已过期、无处可跳（2026-10-25）
        if rec.get("turns", 0) >= 4 or age > 180:
            self._last_rec = None
            return False
        if not self._is_accept_phrase(text):
            rec["turns"] = rec.get("turns", 0) + 1
            return False
        self._last_rec = None
        logger.info("[Jump] 用户确认推荐，跳转 %s", rec.get("module"))
        return await self._emit_jump(rec.get("module"), rec.get("reason", ""))

    async def _try_direct_jump(self, text: str) -> bool:
        """语音/文字**直接导航**（2026-10-24 用户实测"跳转情绪识别失败"）：
        "跳转到情绪识别/去疲劳检测/我想做情绪识别/打开情感数字人"——不需要
        先有推荐，说出导航意图+模块名即跳。问句（"情绪识别是什么/怎么做"）不跳。
        """
        if self.persona_override != "guide":
            return False
        t = text or ""
        # 裸「去」删除（2026-10-25）：单字去命中去年/过去/回去等常用词，
        # "我去年体检血压偏高"会被误跳 health。保留「去+模块名」导航：
        # lookahead 限定去紧跟模块名首字；(?<!过) 再排除"过去"（否则
        # "过去问诊要排很久队"里的"去问诊"仍会命中）。
        if not re.search(
            r"跳转|跳到|调转|调到|转到|跳去|打开|进入|切换到|前往|想去|想做|要做|做个|做一次|测一下|测一测|带我去|帮我去"
            r"|(?<!过)去(?=健康|问诊|体检|疲劳|情绪|情感|数字人|陪伴|测)",
            t,
        ):
            return False
        if re.search(r"是什么|什么意思|怎么测|怎么用|怎么做|如何|介绍|告诉|适合吗|要紧吗", t):
            return False
        for module, names in JUMP_TARGETS.items():
            if any(n in t for n in names):
                logger.info("[Jump] 用户直接导航，跳转 %s（%r）", module, t[:30])
                self._last_rec = None
                return await self._emit_jump(module, "")
        # 指代兜底（2026-10-25 实测"你帮我跳转呀/你给我跳转呀"无人接住）：
        # 明确的跳转动词但句中无模块名——导览场景"帮我跳转/去做检测"的默认
        # 去处就是健康问诊（首页默认入口）。问句已在上面排除。
        if re.search(r"跳转|跳去|前往|带我去|帮我去|做个体检|做个检测|做检测|去检测", t):
            logger.info("[Jump] 指代跳转（无模块名），默认 health（%r）", t[:30])
            self._last_rec = None
            return await self._emit_jump("health", "")
        return False

    async def _emit_jump(self, module: str, reason: str) -> bool:
        if not module:
            return False
        # 只发跳转事件，**不重置会话**（2026-10-25 用户实测"做个疲劳检测，
        # 会话直接清零了"）：会话跨页面继承（回家继续聊）+「你好」才是换访客
        # 信号——跳转清记忆与两条拍板都矛盾。旧逻辑是"推荐→好→跳转=访客
        # 结束"时代的遗留。
        await self._emit({
            "type": "module_jump",
            "module": module,
            "reason": reason,
            "mode": self.mode,
        })
        return True

    @staticmethod
    def _is_refresh_phrase(text: str) -> bool:
        return any(p in (text or "") for p in REFRESH_PHRASES)

    @staticmethod
    def _is_accept_phrase(text: str) -> bool:
        t = (text or "").strip().rstrip("。！？!?~，, ")
        if t in ("好", "行", "嗯", "嗯嗯", "去", "要", "走"):
            return True
        return any(t == p or t.startswith(p) for p in ACCEPT_PHRASES)

    async def _reset_session(self, keep_awake: bool = False) -> None:
        """刷新口令：情绪/推荐/记忆归零，前端 session_reset 同步重置 greet 与卡片。

        ``keep_awake=True``＝「你好」新用户软重置：会话数据照常归零，但**不重新
        武装唤醒门**（用户刚打完招呼，再逼一句"你好"体验断裂）。前端 session_reset
        让 greet 链重新待命，于是暖暖重新打招呼、语气是全新开场。
        """
        self._cancel_current_response()
        # 关键：清掉当前话轮登记与折回缓存——漏清的话 pending 残留，下一句
        # 说话会被 speech_started 折回机制拼上这句的音频（含"重新开始"），
        # 反复触发刷新、永远进不了对话（2026-10-24 诊断日志实锤 gen 粘连）
        self._drop_pending_turn()
        self.carry_audio = None
        self.vad.reset()
        self._last_pcm_at = None
        self.emotions.reset()
        self._emotion_note = ""
        self._last_rec = None
        if not keep_awake:
            self.wake.reset(self.mode)
        try:
            self.llm_memory.clear()
        except Exception:
            logger.exception("[Session] 清空会话记忆失败")
        logger.info(
            "[Session] 会话已重置（%s）",
            "你好软重置·门保持开" if keep_awake else "刷新口令",
        )
        await self._emit({"type": "session_reset", "mode": self.mode})

    @staticmethod
    def _emotion_note_from(evt: dict) -> str:
        """把情绪事件转成给模型的一句上下文。"""
        key = evt.get("emotion")
        if not key or key == "neutral":
            return ""
        label = evt.get("label") or key
        n = int(evt.get("consecutive") or 1)
        tip = {
            "low": "语气要温和、先共情，不要急着给方案",
            "anxious": "先安抚情绪，再给一两个具体可做的小步骤",
            "angry": "先认可感受、不要反驳，语速放慢",
            "somatic": "关心具体不适，建议做健康问诊，不要下诊断",
            "positive": "可以轻松一些，顺势鼓励",
        }.get(key, "")
        return f"用户当前情绪：{label}（连续 {n} 句）。{tip}"

    def _doc_emitter(self, generation: int):
        """Forward [DOC: ...] tags found in the reply stream to the live UI."""
        def on_doc(doc: dict) -> None:
            self._emit_from_thread(
                "json",
                {"type": "doc", **doc, "mode": "live"},
                generation,
            )

        return on_doc

    def _cancel_current_response(self) -> None:
        self.generation += 1
        # live 模式：让在飞的 llm_response 停手（它会把段落直接送进
        # 会话 TTS 队列，光靠 generation 拦不住）。
        self.live_reply_id = getattr(self, "live_reply_id", 0) + 1
        session = session_manager.get_session(self.session_id) if self.session_id else None
        if session is not None:
            session.flush_talk()
            output = getattr(session, "output", None)
            clear_buffer = getattr(output, "clear_buffer", None)
            if clear_buffer is not None:
                clear_buffer()

    def _begin_live_reply(self, generation: int, session=None):
        """开一轮 live 回复，返回 (on_text, should_stop) 回调。

        ``should_stop`` 让被新一轮取代的回复立刻停止供段；``on_text`` 记录
        "数字人正在说什么"，供 ghost 回声判定使用（live 模式此前从不写这两个
        字段，等于回声防护完全失效，数字人会把自己的话听成用户提问）。
        """
        self.live_reply_id += 1
        reply_id = self.live_reply_id
        # 会话代际：HTTP /human 的文字输入会调 begin_new_turn() 抬代际，
        # 这条路径绕开了 WebSocket 的 generation，只有比代际才拦得住。
        epoch_token = getattr(session, "_reply_epoch", None)
        self.reply_text = ""
        self.last_reply_audio_at = None

        def on_text(body: str) -> None:
            self.reply_text += body
            self.last_reply_audio_at = time.monotonic()
            # live 模式也流式回传回复文本：emotionDetect 陪伴页要显示文字，
            # 原版 live 模式只把文本用于回声防护、不回传（页面只能显示占位句）
            self._emit_from_thread(
                "json",
                {"type": "assistant_delta", "text": body, "mode": "live"},
                generation,
            )

        def should_stop() -> bool:
            if reply_id != self.live_reply_id:
                return True
            if generation != self.generation or self.closed.is_set():
                return True
            if epoch_token is not None and session is not None:
                checker = getattr(session, "reply_token_is_current", None)
                if checker is not None and not checker(epoch_token):
                    return True
            return False

        return on_text, should_stop

    def _seconds_since_reply_audio(self, mode: str) -> float | None:
        """距数字人最近一次出声的秒数（None = 本次连接还没说过话）。

        实时口型正在说话时直接给 0：声音此刻就在外放，回声转写必须按
        "刚刚"来判定，而不是按最后一个段落交给 TTS 的时刻。
        """
        if mode == "live":
            session = (
                session_manager.get_session(self.session_id)
                if self.session_id else None
            )
            if session is not None and session.is_speaking():
                return 0.0
        if self.last_reply_audio_at is None:
            return None
        return time.monotonic() - self.last_reply_audio_at

    def _avatar_speaking(self) -> bool:
        """live 模式下数字人是否正在出声（回声门控用）。"""
        if self.mode != "live" or not self.session_id:
            return False
        session = session_manager.get_session(self.session_id)
        return bool(session is not None and session.is_speaking())

    def _default_reply_active(self) -> bool:
        """default 模式最近是否在流出回复音频（回声门控/抢话判定用）。

        导览页跑 default 模式（无 wav2lip/WebRTC，音频按段以 WS 二进制直发），
        没有 session.is_speaking() 可查；用"最后一个 PCM 块发出的时刻"近似
        ——出声期间块持续流动，时间戳始终新鲜，回复发完几秒后自然过期。
        """
        if self.mode != "default" or self.last_reply_audio_at is None:
            return False
        return (time.monotonic() - self.last_reply_audio_at) < GHOST_WINDOW_S

    async def _handle_pcm(self, payload: bytes) -> None:
        # 常听仲裁：default 模式下非持有者的 PCM 直接丢弃（不进 VAD）。
        # 双标签页同开麦时同句语音会走两条连接各触发一轮回复（"两重语音"）；
        # 旧页面通常已在 configure 时收到 mic_taken 关麦，这里兜底防旧版
        # 前端/未处理事件者仍在发流。
        if self.mode == "default" and _MIC_OWNER is not self and _mic_owner_alive():
            return
        now = time.monotonic()
        if (
            self._last_pcm_at is not None
            and now - self._last_pcm_at > 1.5
            and self.vad.speaking
        ):
            # 断流自愈（2026-10-24 实测"正在听你说话"永久悬死）：PCM 中断>1.5s
            # 多为页面后台把 AudioContext 挂起，断点处的"静音"永远收不到——
            # 丢弃僵死话轮（断流期间的语音本就没采到），让新语音重新开轮。
            logger.info("[Mic] PCM 断流 %.1fs，重置僵死话轮", now - self._last_pcm_at)
            self.vad.reset()
            self._drop_pending_turn()
            self.carry_audio = None
        self._last_pcm_at = now
        for event in self.vad.feed(payload):
            if event.kind == "speech_started":
                if self._avatar_speaking() or self._default_reply_active():
                    # 出声期抢话（用户拍板 2026-10-23：说话打断暖暖、回答最新提问）：
                    # 不立刻打断、也不丢话轮（曾丢话轮导致"第二句卡检测"）——继续
                    # 收音，_handle_turn 里 ASR+ghost 判定：转写是新内容（真人声，
                    # 非回声）才打断旧回复并回答；是回声则旧回复继续播、不打扰。
                    # default 模式（导览页）同判：喇叭声被麦克风收回会立刻打断
                    # 在播回复，必须等转写确认非回声再动手。
                    continue
                pending = self.pending_turn
                if pending is not None and (
                    self.pending_task is None or self.pending_task.done()
                ):
                    # 僵尸话轮：登记还在但任务早已结束（空转写/异常的 early-return
                    # 曾漏清，或代际已换代）。绝不能把它的旧音频折进新话轮——
                    # 旧音频多半是回声/无效声，拼上去会毒化转写、循环吞句
                    # （2026-10-24 实测"第二句起全被吞"）。丢弃并走正常开轮。
                    logger.info(
                        "[VAD] 丢弃僵死话轮（gen=%s，任务已结束）",
                        pending.get("generation"),
                    )
                    self.pending_turn = None
                    self.carry_audio = None
                    pending = None
                if pending is not None:
                    logger.info("[VAD] 重开折回：pending 存在（gen=%s）", pending.get("generation"))
                    # Reopened inside the grace window: fold the finished part
                    # back into this utterance instead of interrupting.
                    self.pending_turn = None
                    if self.pending_task is not None:
                        self.pending_task.cancel()
                        self.pending_task = None
                    chunks = [
                        item
                        for item in (self.carry_audio, pending.get("audio"))
                        if item is not None and item.size
                    ]
                    self.carry_audio = (
                        np.concatenate(chunks) if chunks else None
                    )
                else:
                    self._cancel_current_response()
                await self._emit({"type": "speech_started"})
            elif event.kind == "speech_stopped" and event.audio is not None:
                generation = self.generation
                await self._emit({"type": "speech_stopped"})
                audio = event.audio
                if self.carry_audio is not None and self.carry_audio.size:
                    audio = np.concatenate((self.carry_audio, audio))
                    self.carry_audio = None
                task = asyncio.create_task(
                    self._handle_turn(
                        audio,
                        generation,
                        self.mode,
                        self.session_id,
                    )
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _handle_turn(
        self,
        audio: np.ndarray,
        generation: int,
        mode: str,
        session_id: str,
    ) -> None:
        try:
            # Register as pending BEFORE ASR so a reopen can cancel and fold
            # the audio during recognition as well as during the grace wait.
            self.pending_turn = {"generation": generation, "audio": audio}
            self.pending_task = asyncio.current_task()
            grace_s = float(getattr(self.opt, "mic_reopen_grace_ms", 800)) / 1000.0

            logger.info("[Turn] 话轮开始 gen=%s 音频 %.2fs", generation, len(audio) / 16000.0)
            # 音量自愈（2026-10-24 实测：部分标签页/设备链路送达的语音 RMS
            # 低至 0.011~0.021，近乎耳语，ASR 只能转写出空串——"音量条在动
            # 但不识别"的直接原因）。过小音量先归一化放大再送 ASR：
            # 目标 RMS ~0.06，增益上限 8x；低于 0.0015 视为真静音不动。
            if audio.size:
                rms = float(
                    np.sqrt(np.mean(np.square(audio.astype(np.float32) / 32768.0)))
                )
                if 0.0015 < rms < 0.04:
                    gain = min(0.06 / rms, 8.0)
                    audio = np.clip(
                        audio.astype(np.float32) * gain, -32768, 32767
                    ).astype("<i2")
                    logger.info(
                        "[Turn] 音量过小（RMS %.3f），放大 %.1fx 后送 ASR",
                        rms, gain,
                    )
            await self._emit({"type": "pipeline", "stage": "asr"})
            asr_task = asyncio.create_task(
                asyncio.to_thread(self.asr.transcribe_pcm, audio)
            )
            # The grace window runs concurrently with ASR: recognition already
            # takes several hundred milliseconds, so the anti-barge-in wait
            # adds no user-visible latency.
            grace_task = asyncio.create_task(asyncio.sleep(grace_s)) if grace_s > 0 else None
            transcript = await asr_task
            if grace_task is not None:
                await grace_task
            # 转写落日志：排查"说了没反应/没跳转"的第一手证据——此前不记，
            # 只能盲猜措辞被识别成了什么（2026-10-24 连续两次排查都卡在这里）
            if transcript:
                logger.info("[Turn] 转写: %r", transcript)
            if generation != self.generation or not transcript:
                # 关键：清掉本轮登记——空转写的 early-return 若留着 pending，
                # 下一句话会被 speech_started 折回机制拼上这段无效音频，
                # 僵尸轮循环吞句（2026-10-24 实测"第二句永远不识别"根因）
                self.pending_turn = None
                self.pending_task = None
                if not transcript and generation == self.generation:
                    # 空转写是"说唤醒词没反应"的头号嫌疑：打出来才分得清
                    # 是麦克风没收好音还是 ASR 挂了（时长/能量一并记录）
                    rms = float(
                        np.sqrt(np.mean(np.square(audio.astype(np.float32) / 32768.0)))
                    ) if audio.size else 0.0
                    logger.info(
                        "[Turn] 空转写（音频 %.2fs，RMS %.3f）→ 提示重说",
                        len(audio) / 16000.0, rms,
                    )
                    # 连续对话里偶尔没听清是常态，不该走 error；用轻提示让用户
                    # 自然再说一次。**播报中静默丢弃**：此时段的空音频几乎都是
                    # 麦克风收回的回复回声（实测问候播报期间连续 4 条空转写
                    # 提示轰炸前端），不打扰正在听的会话。
                    # **短音频（<1.2s）空转写＝环境音/碰撞声**：静默收口
                    # （quiet hint，不计入前端"没听清"、不进面板），避免
                    # 噪声话轮刷屏并误触发采音重启（2026-10-25 用户反馈
                    # "识别到环境音"）。
                    if not (self._avatar_speaking() or self._default_reply_active()):
                        short_noise = len(audio) < PCM_SAMPLE_RATE * 1.2
                        await self._emit({
                            "type": "hint",
                            "message": "" if short_noise else "没听清，请再说一次",
                            "quiet": short_noise,
                        })
                return
            # 环境音/语气词筛选（2026-10-25 用户反馈）：转写只剩 0~1 个有效字
            # （"嗯。""啊。""呃。"）且音频不足 1.2s＝噪声或无意义语气词——
            # 静默丢弃，不进面板、不进 LLM、不算"没听清"。**确认词一律放行**
            # （好/行/去/要走…封闭小集合，环境音转不出这些字）：确认跳转判定
            # 在筛选之后，先筛掉会让"好"永远到不了跳转（2026-10-26 实测
            # 跳转后再说"好"被误杀——上一次确认已清 _last_rec，豁免失效）；
            # 无推荐可确认时单字"好"由后续逻辑当普通输入处理。
            meaningful = re.sub(r"[^\w\u4e00-\u9fff]+", "", transcript)
            is_confirm_word = self._is_accept_phrase(transcript)
            if (
                len(meaningful) < 2
                and len(audio) < PCM_SAMPLE_RATE * 1.2
                and not is_confirm_word
            ):
                logger.info(
                    "[Turn] 丢弃环境音/语气词（音频 %.2fs，%r）",
                    len(audio) / 16000.0, transcript,
                )
                self.pending_turn = None
                self.pending_task = None
                if not (self._avatar_speaking() or self._default_reply_active()):
                    await self._emit({"type": "hint", "message": "", "quiet": True})
                return
            # 刷新口令（语音路径曾缺失：只有打字路径检查，用户说「重新开始」
            # 被当普通聊天送 LLM，2026-10-24 实测无效）。控制类意图优先于一切。
            # 话轮登记校验 + 热词纠错都必须在唤醒门**之前**：
            # - 校验若排在门后，裸「你好」软重置会清掉 pending_turn，回头校验
            #   直接把这一轮误杀（实测 transcript 丢失、问候无回复）
            # - 纠错若排在门后，"换换"这类误听永远得不到校正、永远唤不醒
            if (
                self.pending_turn is None
                or generation != self.generation
                or self.closed.is_set()
            ):
                if self.pending_turn is None and not self.closed.is_set():
                    # 被 cancel/前端打断作废的活话轮：必须给前端一个收尾事件，
                    # 否则 busy 永久卡死——每个新 speech_started 又触发前端自动
                    # 打断→cancel→再杀新话轮，自毁循环（2026-10-25 实测
                    # 转写成功后话轮无声消失、"又卡住"的根因）
                    await self._emit(
                        {"type": "hint", "message": "刚才被打断了，请再说一次"}
                    )
                return
            self.pending_turn = None
            self.pending_task = None

            from server.personas import get_active_persona

            # 热词纠错用当前模式自己的人设（两套人设名字不同）
            persona = get_active_persona(mode)
            if persona is not None:
                corrected = persona.correct(transcript)
                if corrected != transcript:
                    logger.info(
                        "[Mic] 热词校正: %r -> %r", transcript, corrected
                    )
                    transcript = corrected

            # 回声抑制必须在唤醒门**之前**：问候/回复外放被麦克风收回的转写
            # （含回声里的"你好"）先在这里按 ghost 丢弃，否则会假唤醒开门
            if is_ghost_transcript(
                transcript,
                self.reply_text,
                self._seconds_since_reply_audio(mode),
                window=(
                    GHOST_WINDOW_LIVE_S if mode == "live" else GHOST_WINDOW_S
                ),
            ):
                logger.info("[Mic] ghost turn suppressed (%s): %r", mode, transcript)
                if not (self._avatar_speaking() or self._default_reply_active()):
                    # 只有用户确实在说话时才提示重说；出声期的纯回声静默丢弃
                    # （default 模式 _avatar_speaking 恒 False，须并上 _default_reply_active）
                    await self._emit({"type": "hint", "message": "刚才有点回声，请再说一次"})
                return

            # 唤醒词门控（2026-10-25 用户拍板"检测到你好之前什么都当没听到"，
            # 从跳转/刷新之后提到**一切语音判定之前**）：未唤醒时这轮语音整体
            # 丢弃——不跳转、不刷新、不打断在播内容、不进 LLM。此前直接导航
            # 与确认跳转排在门控前，用户没说你好也能被一句话跳走（实测
            # '我要做疲劳检测。'未唤醒直接跳转 fatigue）。唤醒词后的裸问候
            # 软重置逻辑不变。
            proceed, message, woke = self.wake.check(mode, transcript)
            if not proceed:
                logger.info(
                    "[Wake] 未听到唤醒词 %r，忽略: %r",
                    self.wake.word_for(mode), transcript,
                )
                await self._emit(
                    {
                        "type": "wake_word_pending",
                        "text": transcript,
                        "wakeWord": self.wake.word_for(mode),
                        "mode": mode,
                    }
                )
                return
            if woke:
                logger.info("[Wake] %s 模式已唤醒", mode)
            # 「你好」＝新一位用户（2026-10-24 用户拍板）：**只有裸问候**
            # （整句就是唤醒词、或唤醒词后只剩"你好"）才软重置——清掉上一位
            # 用户的记忆/情绪/推荐，唤醒门保持开，前端重新播 greet。带实质
            # 内容的"你好，我想做检测"是普通提问，记忆照常保留。
            # 2026-10-25 补：重置判定不能只在 woke（门刚开）时做——已唤醒
            # 状态下再说「你好」同样要重置（实测会话中再问好被当普通聊天、
            # 会话没清）。裸问候判定对两种路径统一生效。
            rest_is_bare = not message or is_bare_greeting(
                message, self.wake.word_for(mode)
            )
            if rest_is_bare:
                await self._reset_session(keep_awake=True)
                # 关键：软重置内部抬了代际（作废旧回复），本轮必须重取新代际
                # ——继续用旧值的话，回复工人比对"代际不符"会把这一轮的
                # 问候回复当场掐死（用户说「你好」后全程沉默的回归根因，
                # 2026-10-24 日志实锤：唤醒→重置→无 DefaultReply）
                generation = self.generation
            if woke or rest_is_bare:
                # 非 woke 的裸问候（已唤醒中再说「你好」）也必须发 wake：前端
                # session_reset 处理器会先把面板记录门归 false，全靠随后这条
                # wake 置回——漏发则面板从此静音、什么都不记录（2026-10-25
                # 实测"麦克风框完全不记录asr结果"）
                await self._emit(
                    {"type": "wake", "word": self.wake.word_for(mode), "mode": mode}
                )
            transcript = message

            if self._is_refresh_phrase(transcript):
                await self._reset_session()
                return

            # 出声期抢话确认：转写是新的用户语音（过了 ghost=非回声）→
            # 此刻才打断旧回复（flush_talk 清掉在播/排队的旧语音），
            # 回答最新这条提问。重取代际，本轮后续检查用新值。
            if self._avatar_speaking() or self._default_reply_active():
                self._cancel_current_response()
                generation = self.generation
                logger.info("[Mic] 语音抢话：打断在播回复，回答最新提问")

            # 直接导航（"跳转到情绪识别"）优先于推荐确认——没有待确认推荐
            # 时也能跳；确认词（"好/可以"）仍走 _try_confirm_jump。
            # 位置：必须在 ghost 回声抑制之后——guide 推荐回复（"建议做一次X
            # 检测，要做就说声好"）句内含导航动词+模块名+确认词，导览页外放被
            # 麦克风收回的回声转写若先到跳转判定，永远轮不到 ghost 抑制，页面
            # 就会无人自跳（2026-10-25）。真人新话轮不过 ghost，直跳行为不变；
            # 确认词（"好"）句长 < GHOST_MIN_CHARS 被 is_ghost_transcript 直接
            # 放行，"说好确认跳"不受影响。
            # 唤醒门控已在最前面拦下未唤醒的一切（见上），此处必已唤醒。
            if await self._try_direct_jump(transcript):
                return
            # 模块推荐确认跳转（语音路径曾缺失：用户说「好」被当普通聊天，
            # 2026-10-23 用户实测"说跳转他还在对话"）
            if await self._try_confirm_jump(transcript):
                return

            await self._emit({"type": "transcript", "text": transcript, "mode": mode})

            # 情绪跟踪：分类这句用户原话；变化/连续同情绪时给前端发事件，
            # 连续两次同一负向情绪会附带 recommend（页面弹资料卡）
            try:
                for evt in self.emotions.feed(transcript):
                    if evt.get("type") == "recommend":
                        evt["action"] = self.opt.avatar_id if hasattr(self.opt, "avatar_id") else ""
                        if evt.get("kind") == "module":
                            self._last_rec = {
                                "module": evt.get("module"),
                                "reason": evt.get("reason", ""),
                                "at": time.monotonic(),
                                "turns": 0,
                            }
                    if evt.get("type") == "emotion":
                        self._emotion_note = self._emotion_note_from(evt)
                    await self._emit(evt)
                    logger.info("[Emotion] %s", evt)
            except Exception:
                logger.exception("[Emotion] 情绪跟踪失败（不影响对话）")

            if mode == "live":
                session = session_manager.get_session(session_id)
                if session is None:
                    await self._emit({"type": "error", "message": "实时口型连接尚未就绪"})
                    return
                await self._emit({"type": "pipeline", "stage": "llm"})
                on_text, should_stop = self._begin_live_reply(generation, session)
                await asyncio.to_thread(
                    llm_response, transcript, session, {},
                    self._live_action_emitter(generation),
                    on_text=on_text,
                    should_stop=should_stop,
                    on_doc=self._doc_emitter(generation),
                    context_note=self._emotion_note,
                    persona_override=self.persona_override,
                    auto_doc=self.persona_override != "guide",
                )
                if generation == self.generation:
                    await self._emit({"type": "response_accepted", "mode": "live"})
                return

            await self._emit({"type": "pipeline", "stage": "llm"})
            await self._run_default_reply(transcript, generation)
        except Exception as exc:
            logger.exception("Streaming microphone turn failed")
            if generation == self.generation:
                await self._emit({"type": "error", "message": f"处理失败：{exc}"})

    async def _run_default_reply(
        self, text: str, generation: int, canned_reply: str = ""
    ) -> None:
        """带硬超时的 default 回复执行器。

        LLM 客户端自身有 20s 超时，但 DNS 解析挂起不在 httpx 超时覆盖内；
        断网抖动时 _stream_default_reply 会无限挂起，前端停在"正在听你说话/
        正在思考"（2026-10-25 实测 deepseek 时通时断、话轮悬死无任何日志）。
        75s 硬超时兜底：抬代际让僵尸线程后续输出全部被丢，发 error 收口前端。
        """
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._stream_default_reply, text, generation, canned_reply
                ),
                timeout=75.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[DefaultReply] 回复超时 75s（网络/LLM 挂起），收口")
            self._cancel_current_response()
            if not self.closed.is_set():
                await self._emit(
                    {"type": "error", "message": "回复超时了，请再问一次"}
                )

    def _make_default_tts(self):
        """Voice engine for the WS 直发链路（导览页 + 轻量默认页）.

        Local CosyVoice3, 暖暖统一云霞卡通音色 guide_boy_v1（2026-10-24
        用户拍板：旧默认页男孩音色 v5 已删除，default_page_voice 即云霞卡通）。
        ``--default_tts edge`` opts into the online voice instead.
        """
        if getattr(self.opt, "default_tts", "cosyvoice3") == "edge":
            from tts.edge import EdgeTTSVoice

            return EdgeTTSVoice(self.opt)
        from tts.cosyvoice3 import CosyVoice3TTS

        return CosyVoice3TTS(self.opt, parent=None, default_page_voice=True)

    def _stream_default_reply(self, text: str, generation: int, canned_reply: str = "") -> None:
        tts = self._make_default_tts()
        tts_name = (
            "cosyvoice3"
            if getattr(self.opt, "default_tts", "edge") == "cosyvoice3"
            else "edge"
        )
        # 语义动作是实时口型模式专属：轻量页不提示、不驱动，但保留
        # 解析器兜底剥离 LLM 偶发的动作标签，避免被朗读出来
        parser = ActionTagParser()
        slicer = SegmentSlicer(
            FIRST_SEGMENT_MIN, FIRST_SEGMENT_MAX, FIRST_SEGMENT_MAX + 8,
            SEGMENT_MIN, SEGMENT_MAX, SEGMENT_MAX + 24,
            first_weak_min=FIRST_SEGMENT_MIN,
            weak_min=14,
        )
        skip_announced = False
        audio_started = False
        rec_announced: set[str] = set()  # 本轮回复已发过推荐事件的模块（去重）
        self.reply_text = ""
        self.last_reply_audio_at = None
        # 导览页（persona=guide）也走 default 模式：画面是前端动画段播放，
        # 不需要 wav2lip/WebRTC，语音按段以 WS 二进制直发。人设换成暖暖
        # 导览员并启用资料卡标签（[DOC:]），情绪上下文与 live 路径同源；
        # 轻量页（无 persona）行为保持不变。
        guide = self.persona_override == "guide"
        doc_stripper = None
        if guide:
            persona = get_persona("guide")
            base_prompt = (
                persona.prompt
                if persona is not None and persona.prompt
                else active_system_prompt(mode="default")
            )
            system_prompt = base_prompt + DOC_PROMPT_RULES
            if self._emotion_note:
                system_prompt += "\n" + self._emotion_note.strip()
            doc_stripper = DocTagStripper(self._doc_emitter(generation))
        else:
            system_prompt = (
                # 轻量页的人设（personas/*.md 里 mode: default）
                active_system_prompt(mode="default")
                + "\n回答要求：直接口语化回答，一般控制在80字以内，不要输出JSON，不要使用Markdown。"
            )

        def speak(text: str) -> None:
            """Synthesize one segment with one in-place retry.

            The lightweight page uses the configured engine (local CosyVoice3
            by default — reliable; Edge opt-in). On transient failure the
            segment retries once; persistent failure falls back to the
            offline Windows voice so a reply is never silenced.
            """
            nonlocal audio_started, skip_announced
            for attempt in range(3):
                try:
                    audio_started, samples = self._speak_segment(
                        tts, text, generation, audio_started
                    )
                    if samples > 0:
                        return
                    if generation != self.generation or self.closed.is_set():
                        return  # 回复已取消，不再重试
                    logger.warning(
                        "[DefaultTTS] %s 本段 0 音频样本", tts_name
                    )
                except Exception as exc:
                    if generation != self.generation or self.closed.is_set():
                        return
                    logger.warning(
                        "[DefaultTTS] %s 合成失败: %s", tts_name, exc
                    )
                if attempt == 0:
                    time.sleep(0.35)  # 瞬断重试一次
                    continue
                # 主引擎重试仍失败：本段改用本地 Windows 语音，不再哑
                try:
                    logger.warning(
                        "[DefaultTTS] %s 持续失败，本段用本地 Windows 语音",
                        tts_name,
                    )
                    if not skip_announced:
                        skip_announced = True
                        self._emit_from_thread(
                            "json", {"type": "tts_fallback", "to": "windows"}, generation
                        )
                    audio_started, samples = self._speak_segment(
                        _windows_fallback_tts(), text, generation, audio_started
                    )
                except Exception:
                    logger.exception("[DefaultTTS] 本地 Windows 语音也失败")
                return

        # 仅一个语音消费者，保持顺序；LLM 接收不再被整段 TTS 阻塞。
        # 有界队列限制长回复积压，取消时不把上一轮剩余段落继续送去合成。
        segments = queue.Queue(maxsize=8)
        producer_done = threading.Event()
        aborted = threading.Event()
        worker_errors = []
        reply_started = time.perf_counter()
        first_text = True

        def cancelled() -> bool:
            return (aborted.is_set() or generation != self.generation
                    or self.closed.is_set())

        def enqueue(segment: str) -> None:
            while not cancelled():
                try:
                    segments.put(segment, timeout=0.05)
                    return
                except queue.Full:
                    continue

        def consume() -> None:
            try:
                while not cancelled():
                    try:
                        segment = segments.get(timeout=0.05)
                    except queue.Empty:
                        if producer_done.is_set():
                            return
                        continue
                    if not cancelled():
                        speak(segment)
            except Exception as exc:
                worker_errors.append(exc)
                aborted.set()

        worker = threading.Thread(target=consume, name="default-tts", daemon=True)
        worker.start()

        def deliver(body: str) -> None:
            """正文直达：回传文本 + 切段送 TTS（不做标签剥离）。"""
            if not body:
                return
            self._emit_from_thread(
                "json",
                {"type": "assistant_delta", "text": body},
                generation,
            )
            self.reply_text += body
            # LLM 文本主动推荐模块（不经过情绪跟踪器）时：登记待确认 + 给前端
            # 发 recommend 事件同步推荐卡（2026-10-26 用户反馈"推荐和资料卡
            # 不同步"——语音里推荐了，卡片没动，用户答「好」时卡还停在旧内容）。
            # 每个模块每轮回复只发一次事件（文本流分块会反复命中同一模块名）。
            if guide:
                for mod, names in JUMP_TARGETS.items():
                    if any(n in body for n in names):
                        reason = LLM_REC_REASONS.get(mod, "")
                        self._last_rec = {
                            "module": mod,
                            "reason": reason,
                            "at": time.monotonic(),
                            "turns": 0,
                        }
                        if mod not in rec_announced:
                            rec_announced.add(mod)
                            self._emit_from_thread(
                                "json",
                                {
                                    "type": "recommend",
                                    "kind": "module",
                                    "module": mod,
                                    "reason": reason,
                                },
                                generation,
                            )
                        break
            # 软边界切句：优先标点处切段；超上限后等标点，整段无标点
            # 才在硬上限兜底切断（避免"说话"被劈成两段）
            for segment in slicer.feed(body):
                enqueue(segment)

        def publish(body: str) -> None:
            # 资料标签绝不能被念出来（导览页）：先过剥离器再送出
            if not body:
                return
            if doc_stripper is not None:
                body = doc_stripper.feed(body)
                if not body:
                    return
            deliver(body)

        text_stream = None
        try:
            if canned_reply:
                # 预置播报（不调 LLM、不写会话记忆）：如检测结果回读。
                # 仍过动作/资料标签剥离，行为与 live 的 canned 路径一致。
                text_stream = iter([canned_reply])
            else:
                text_stream = LLMService(self.opt).stream_text(
                    text,
                    system_prompt=system_prompt,
                    memory=getattr(self, "llm_memory", None),
                )
            for delta in text_stream:
                if cancelled():
                    break
                if first_text:
                    logger.info("[DefaultReply] 首字 %.3fs", time.perf_counter() - reply_started)
                    first_text = False
                publish(parser.feed(delta))
            if not cancelled():
                publish(parser.flush())
                if doc_stripper is not None:
                    # flush 的残留直接送出，不再过 feed（feed 会把短尾巴
                    # 当疑似标签前缀扣住，导致最后几个字丢失）
                    deliver(doc_stripper.flush())
                tail = slicer.flush()
                if tail:
                    enqueue(tail)
                logger.info("[DefaultReply] 文本接收完成 %.3fs", time.perf_counter() - reply_started)
        except BaseException:
            aborted.set()
            raise
        finally:
            # 先发完成信号，确保关闭上游异常时消费者也能退出。
            producer_done.set()
            try:
                close = getattr(text_stream, "close", None)
                if close is not None:
                    close()
            finally:
                worker.join()
        if worker_errors:
            raise worker_errors[0]
        if not cancelled():
            self._emit_from_thread(
                "json", {"type": "response_done"}, generation
            )

    def _speak_segment(
        self,
        tts,
        text: str,
        generation: int,
        audio_started: bool,
    ) -> tuple[bool, int]:
        """Synthesize one segment; return (audio_started, sample count).

        段内引擎异常不向上抛：已经送出的音频样本数要如实返回，
        让上层知道"这段已部分播出"——整段重试会把开头再念一遍（重复）。
        """
        carry = np.empty(0, dtype=np.int16)
        samples_out = 0
        generator = tts._generate(text)
        try:
            for samples in generator:
                if generation != self.generation or self.closed.is_set():
                    return audio_started, samples_out
                if not audio_started:
                    # 首块音频真正生成后才宣告，避免失败时误报"正在播放"
                    self._emit_from_thread(
                        "json", {"type": "audio_start"}, generation
                    )
                    audio_started = True
                self.last_reply_audio_at = time.monotonic()
                pcm = np.clip(
                    np.asarray(samples).reshape(-1) * 32768,
                    -32768,
                    32767,
                ).astype("<i2")
                samples_out += pcm.size
                if carry.size:
                    pcm = np.concatenate((carry, pcm))
                offset = 0
                while pcm.size - offset >= PCM_BLOCK_SAMPLES:
                    block = pcm[offset : offset + PCM_BLOCK_SAMPLES]
                    if not self._emit_from_thread(
                        "binary", b"\x01" + block.tobytes(), generation
                    ):
                        return audio_started, samples_out
                    offset += PCM_BLOCK_SAMPLES
                carry = pcm[offset:].copy()
            if carry.size and generation == self.generation:
                self._emit_from_thread(
                    "binary", b"\x01" + carry.tobytes(), generation
                )
        except Exception as exc:
            logger.warning(
                "[DefaultTTS] 段内中断（已送出 %d 样本，不重试避免重复）: %s",
                samples_out, exc,
            )
        finally:
            generator.close()
        return audio_started, samples_out

    async def _handle_text(self, data: dict) -> None:
        event_type = data.get("type")
        if event_type == "configure":
            mode = str(data.get("mode", "default"))
            self.mode = mode if mode in {"default", "live"} else "default"
            self.session_id = str(data.get("sessionid", ""))
            # 连接级唤醒词（导览页传"暖暖"）：常听模式下说唤醒词进入会话；
            # 不传则用模式默认（你好），陪伴页行为不变
            wake_word = str(data.get("wake_word", "") or "").strip()
            if wake_word:
                self.wake.words[self.mode] = wake_word
                self.wake.reset(self.mode)
            # 页面令牌（新版前端每次页面加载生成；旧版不传＝僵尸）
            self.page_token = str(data.get("page_token", "") or "")
            # 连接级人设（如导览页的 guide）：同时决定情绪推荐的角色分工
            persona = str(data.get("persona", "") or "").strip()
            if persona and persona != self.persona_override:
                self.persona_override = persona
                # 直接换新 tracker：triage 只在 guide 角色构造时创建，
                # 只改 role 属性的话 triage 仍是 None，feed 里静默抛错（实测踩过）
                self.emotions = EmotionTracker(
                    role="guide" if persona == "guide" else "companion"
                )
            # 进入某个模式时该模式的唤醒门控重新待命
            self.wake.reset(self.mode)
            # 常听仲裁（2026-10-25 升级为令牌制）：default 模式最新 configure
            # 的连接接管麦克风；旧持有者收 mic_taken。**令牌规则**：带
            # page_token 的新版页面永远优先——无令牌的旧版连接（引擎重启后
            # 自动重连的僵尸页面）不得从有令牌的现任手里抢麦克风，只配静音。
            # 否则隐形僵尸赢了赛跑，用户的语音与回复全流向无人观看的页面，
            # 表现为"说你好没用"（2026-10-25 实测两轮）。
            if self.mode == "default":
                global _MIC_OWNER
                previous = _MIC_OWNER
                prev_alive = previous is not None and previous is not self and not previous.closed.is_set()
                prev_token = getattr(previous, "page_token", "") if previous is not None else ""
                if prev_alive and prev_token and not self.page_token:
                    # 拒绝必须终局：绝不能发 configured——旧版前端把 configured 当
                    # "连接成功"会立刻重新 getUserMedia 抢物理麦克风（引擎丢它的
                    # PCM，但设备被它占住，现任页面开麦永久 NotReadableError）。
                    # 发 mic_taken 后直接断开 WS，旧页面无从得知"已配置成功"，
                    # 它的重连每 2.5s 被拒一次即被断一次，麦克风保持释放。
                    try:
                        self.output.put_nowait(
                            ("json", {"type": "mic_taken", "mode": "default"}, None)
                        )
                        logger.info("[Mic] 仲裁：无令牌的旧版连接被拒（现任持令牌），断开")
                    except Exception:
                        pass
                    try:
                        await self.ws.close()
                    except Exception:
                        pass
                    return
                if prev_alive:
                    notified = False
                    try:
                        previous.output.put_nowait(
                            ("json", {"type": "mic_taken", "mode": "default"}, None)
                        )
                        notified = True
                        logger.info("[Mic] 麦克风仲裁：新连接接管，旧连接静音")
                    except Exception:
                        pass
                    if not notified:
                        # mic_taken 入队失败（旧连接输出队列满/发送任务卡死）时旧页面
                        # 永远收不到让位通知：不停麦、不自关闭，物理麦克风被它占死。
                        # 强制断开它的 WS——各版本前端的 close 处理器都会停麦释放设备。
                        logger.warning(
                            "[Mic] 仲裁通知入队失败，强制断开旧连接以释放麦克风"
                        )
                        previous.closed.set()
                        try:
                            asyncio.create_task(previous.ws.close())
                        except Exception:
                            pass
                _MIC_OWNER = self
            await self._emit({
                "type": "configured",
                "mode": self.mode,
                "wakeRequired": self.wake.required(self.mode),
                "wakeWord": self.wake.word_for(self.mode),
            })
        elif event_type == "awake":
            # 用户点击了麦克风按钮 = 明确的对话意图（与文字输入同等性质），
            # 直接开闸；否则第一句不含"你好"会被门控丢掉，对话体验断裂。
            self.wake.open(self.mode)
            await self._emit({"type": "awake", "mode": self.mode})
        elif event_type == "reset":
            # 用户刷新口令/重新开始：清空情绪、推荐、会话记忆
            await self._reset_session()
        elif event_type == "speak":
            # 预置播报（不调 LLM、不写记忆）：如检测结果回读
            text = str(data.get("text", "")).strip()
            if not text:
                return
            await self._emit({"type": "transcript", "text": text, "mode": self.mode})
            if self.mode == "live":
                session = session_manager.get_session(self.session_id)
                if session is None:
                    await self._emit({"type": "error", "message": "实时口型连接尚未就绪"})
                    return
                on_text, should_stop = self._begin_live_reply(self.generation, session)
                await asyncio.to_thread(
                    llm_response, text, session, {},
                    self._live_action_emitter(self.generation),
                    on_text=on_text,
                    should_stop=should_stop,
                    on_doc=self._doc_emitter(self.generation),
                    context_note="",
                    persona_override=self.persona_override,
                    auto_doc=self.persona_override != "guide",
                    canned_reply=text,
                )
            else:
                # default 模式（导览页）：同样的预置播报，走 WS 直发链路
                task = asyncio.create_task(
                    self._run_default_reply(
                        text, self.generation, canned_reply=text
                    )
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        elif event_type == "cancel":
            self._cancel_current_response()
            self._drop_pending_turn()
            self.vad.reset()
        elif event_type == "text":
            text = str(data.get("text", "")).strip()
            if not text:
                return
            if self._is_refresh_phrase(text):
                await self._reset_session()
                return
            # 「你好」新用户握手（打字路径同样生效）：会话中单独发一句问候
            # 即软重置，让下一位用户无缝接手（不唤醒时不误触发——那属于
            # 正常聊天内容，只有已开门的会话才把裸问候当换用户）。
            if self.mode == "default" and not self.wake.required(self.mode) and is_bare_greeting(
                text, self.wake.word_for(self.mode)
            ):
                await self._reset_session(keep_awake=True)
                await self._emit(
                    {"type": "wake", "word": self.wake.word_for(self.mode), "mode": self.mode}
                )
                await self._emit({"type": "transcript", "text": text, "mode": self.mode})
                return
            # 直接导航/推荐确认都在 transcript 之前判定：跳转就不该起说话动画
            if await self._try_direct_jump(text):
                return
            if await self._try_confirm_jump(text):
                return
            self._cancel_current_response()
            generation = self.generation
            await self._emit({"type": "transcript", "text": text, "mode": self.mode})
            try:
                for evt in self.emotions.feed(text):
                    if evt.get("type") == "recommend" and evt.get("kind") == "module":
                        self._last_rec = {
                            "module": evt.get("module"),
                            "reason": evt.get("reason", ""),
                            "at": time.monotonic(),
                            "turns": 0,
                        }
                    if evt.get("type") == "emotion":
                        self._emotion_note = self._emotion_note_from(evt)
                    await self._emit(evt)
            except Exception:
                logger.exception("[Emotion] 情绪跟踪失败（不影响对话）")
            if self.mode == "live":
                session = session_manager.get_session(self.session_id)
                if session is None:
                    await self._emit({"type": "error", "message": "实时口型连接尚未就绪"})
                    return
                on_text, should_stop = self._begin_live_reply(generation, session)
                task = asyncio.create_task(
                    asyncio.to_thread(
                        llm_response, text, session, {},
                        self._live_action_emitter(generation),
                        on_text=on_text,
                        should_stop=should_stop,
                        on_doc=self._doc_emitter(generation),
                        context_note=self._emotion_note,
                        persona_override=self.persona_override,
                        auto_doc=self.persona_override != "guide",
                    )
                )
            else:
                task = asyncio.create_task(
                    self._run_default_reply(text, generation)
                )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _vad_watchdog(self) -> None:
        """时间型看门狗：话轮开着但 PCM 停止到达超 3s（页面后台挂起且
        resume 被拒等），直接重置僵死话轮——断流自愈依赖下一帧 PCM，
        断供型僵死永远等不到那一帧（2026-10-24 实测复发）。"""
        while not self.closed.is_set():
            await asyncio.sleep(1.0)
            if (
                self.vad.speaking
                and self._last_pcm_at is not None
                and time.monotonic() - self._last_pcm_at > 3.0
            ):
                logger.info(
                    "[Mic] 看门狗：话轮悬死 %.1fs 无 PCM，重置",
                    time.monotonic() - self._last_pcm_at,
                )
                self.vad.reset()
                self._drop_pending_turn()
                self.carry_audio = None
                self._last_pcm_at = None

    async def run(self) -> None:
        sender = asyncio.create_task(self._sender())
        watchdog = asyncio.create_task(self._vad_watchdog())
        await self._emit({"type": "ready", "sampleRate": PCM_SAMPLE_RATE})
        try:
            async for message in self.ws:
                if message.type == WSMsgType.BINARY:
                    await self._handle_pcm(bytes(message.data))
                elif message.type == WSMsgType.TEXT:
                    try:
                        await self._handle_text(json.loads(message.data))
                    except json.JSONDecodeError:
                        continue
                elif message.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                    break
        finally:
            self.closed.set()
            # 常听仲裁：持有者断开时释放所有权，后续第一个 configure/PCM 接管
            global _MIC_OWNER
            if _MIC_OWNER is self:
                _MIC_OWNER = None
                logger.info("[Mic] 持有者连接断开，麦克风仲裁释放")
            self._cancel_current_response()
            self._drop_pending_turn()
            for task in self._tasks:
                task.cancel()
            sender.cancel()
            watchdog.cancel()
            await asyncio.gather(sender, watchdog, return_exceptions=True)


class _FallbackASR:
    """FunASR 优先、失败/不可用自动回退本地 Whisper。接口与两者一致。"""

    def __init__(self, opt) -> None:
        self.funasr = ResidentFunASR()
        self.whisper = ResidentWhisper(getattr(opt, "asr_model", "small"))
        self._whisper_loaded = False

    def warmup(self) -> None:
        try:
            self.whisper._ensure_model()
            self._whisper_loaded = True
        except Exception:
            logger.exception("[Mic ASR] whisper 预热失败（回退链可能不可用）")

    def transcribe_pcm(self, pcm: np.ndarray) -> str:
        if self.funasr._ok:
            text = self.funasr.transcribe_pcm(pcm)
            if text:
                return text
        return self.whisper.transcribe_pcm(pcm)


def setup_microphone_routes(app: web.Application, opt) -> None:
    backend = getattr(opt, "asr_backend", "funasr-http")
    if backend == "funasr-http":
        asr = _FallbackASR(opt)
    else:
        asr = ResidentWhisper(getattr(opt, "asr_model", "small"))
    app["resident_asr"] = asr

    async def microphone_ws(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2 * 1024 * 1024)
        await ws.prepare(request)
        await MicrophoneConnection(ws, opt, asr).run()
        return ws

    async def warm_asr(_app: web.Application) -> None:
        async def warm() -> None:
            try:
                await asyncio.to_thread(asr.warmup)
            except Exception:
                logger.exception("Resident microphone ASR warmup failed")

        _app["resident_asr_warm_task"] = asyncio.create_task(warm())

    app.router.add_get("/ws/conversation", microphone_ws)
    app.on_startup.append(warm_asr)
