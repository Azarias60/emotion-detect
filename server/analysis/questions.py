"""问答能力：逐题回答判定与推进规则。

策略（QuestionTiming）沿用既有回答判定，以单调毫秒时钟表达：展示确认
后 3 秒首次转写，空结果 2 秒重取，有文字后等待 2 秒收尾，单题最多 15 秒。
流程（QuestionFlow）消费采集轮次的同源音频切片完成转写，把题目、临时
转写与逐题事实提供给所属报告；60 秒采集时序归检测调度，不在此处。
"""
import asyncio
import time

from server.compute.speech.service import ASR_SAMPLE_RATE
from server.compute.speech.types import AudioData
from server.concurrency import run_blocking
from server.detection_errors import DetectionInputError
from server.support.reporting.types.errors import ReportNotFoundError

# 展示确认后的轮询粒度：与旧页面 250ms tick 的判定节奏一致。
POLL_SECONDS = 0.2


class QuestionTiming:
    """一道题的判定状态机；时间全部由调用方以单调毫秒传入。"""

    FIRST_TRANSCRIBE_MS = 3000
    RETRY_MS = 2000
    SETTLE_MS = 2000
    LIMIT_MS = 15000

    def __init__(self):
        self._started = None
        self._next = None
        self._action = None

    def start(self, now_ms):
        """题目展示确认：起点固定，下一动作是首次转写。"""
        self._started = now_ms
        self._action = "transcribe"
        self._next = now_ms + self.FIRST_TRANSCRIBE_MS

    def transcribed(self, text, now_ms):
        """一次转写结果推进判定：空文本重取，有文本后等待收尾。"""
        if text.strip():
            self._action = "close"
            self._next = now_ms + self.SETTLE_MS
        else:
            self._action = "transcribe"
            self._next = now_ms + self.RETRY_MS

    def next_action(self, now_ms):
        """到点返回 "transcribe"/"close"；未确认展示或未到点返回 None。"""
        if self._next is None:
            return None
        if self._action == "transcribe" and now_ms >= self._started + self.LIMIT_MS:
            return "close"
        return self._action if now_ms >= self._next else None


def transcript_text(result):
    """切片转写的回答文本；缓冲无音频或未识别出内容都是空串。"""
    if result is None:
        return ""
    return "".join(segment.text for segment in result.segments).strip()


class _QuestionRound:
    def __init__(self, report_id, questions):
        self.report_id = report_id
        self.questions = tuple(questions)
        self.answers: list[tuple[str, str]] = []
        self.confirmed = None
        self.stopped = False
        self.wake = asyncio.Event()
        self.task: asyncio.Task | None = None


class QuestionFlow:
    """一轮检测的问答执行；音频来自采集轮次，事实交给所属报告。"""

    def __init__(self, reports, rounds, speech, admission):
        self._reports = reports
        self._rounds = rounds
        self._speech = speech
        self._admission = admission
        self._rounds_by_id: dict[int, _QuestionRound] = {}

    async def begin(self, report_id, questions):
        round = _QuestionRound(report_id, tuple(questions))
        self._rounds_by_id[report_id] = round
        self._publish(round, {"questions": list(round.questions)})
        round.task = asyncio.create_task(self._run(round))

    def displayed(self, report_id, index):
        """题目已展示反馈：只受理与推进序号一致的确认，激活计时起点。"""
        round = self._rounds_by_id.get(report_id)
        if round is None or round.stopped or index != len(round.answers):
            return
        round.confirmed = index
        round.wake.set()

    async def finish(self, report_id):
        """封存后的回答交接：停止推进，结清活动题，返回已结清问答。"""
        round = self._rounds_by_id.get(report_id)
        if round is None:
            return ()
        round.stopped = True
        round.wake.set()
        if round.task is not None:
            await round.task
        return tuple(round.answers)

    async def cancel(self, report_id):
        round = self._rounds_by_id.pop(report_id, None)
        if round is None:
            return
        round.stopped = True
        round.wake.set()
        if round.task is not None:
            round.task.cancel()

    # ------------------------------------------------------------------
    async def _run(self, round):
        try:
            while not round.stopped and len(round.answers) < len(round.questions):
                while round.confirmed is None and not round.stopped:
                    await round.wake.wait()
                    round.wake.clear()
                if round.stopped:
                    return
                index = round.confirmed
                round.confirmed = None
                await self._ask(round, index)
        except asyncio.CancelledError:
            raise
        except DetectionInputError:
            # 本轮采集输入失效（设备换代等）：停止推进，封存交由调度裁决。
            round.stopped = True

    async def _ask(self, round, index):
        recording = self._rounds.active()
        if recording is None:
            raise DetectionInputError("本轮没有进行中的采集记录")
        started_ms = recording.position_ms()
        policy = QuestionTiming()
        policy.start(time.monotonic() * 1000)
        while True:
            await asyncio.sleep(POLL_SECONDS)
            if round.stopped:
                return
            now_ms = time.monotonic() * 1000
            action = policy.next_action(now_ms)
            if action is None:
                continue
            text = await self._transcribe(recording, started_ms, recording.position_ms())
            if round.stopped:
                return
            policy.transcribed(text, now_ms)
            if action == "close":
                round.answers.append((round.questions[index], text))
                self._publish(round, {
                    "answers": [{"question": q, "answer": a} for q, a in round.answers],
                    "transcript": None,
                })
                return
            if text:
                self._publish(round, {"transcript": {"index": index, "text": text}})

    async def _transcribe(self, recording, start_ms, end_ms):
        window = recording.audio_segment(start_ms, end_ms, ASR_SAMPLE_RATE)
        if window is None:
            return ""
        audio = AudioData(window.pcm, window.sample_rate)
        with self._admission.acquire("speech_slice", "正在识别语音"):
            return transcript_text(await run_blocking(self._speech.result, audio))

    def _publish(self, round, facts):
        try:
            self._reports.record_facts(round.report_id, facts)
        except ReportNotFoundError:
            # 报告已被移除（本轮已取消或删除）：停止推进，不复活记录。
            round.stopped = True
