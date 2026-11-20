"""后端检测调度：从建立报告开始，安排准备、采集、问答、封存与后续处理。

一次执行开始时绑定账号与报告目标，后续产出始终归该报告；封存前的工作
依赖使用条件（身份、页面与模式），条件失效即取消并移除未完成报告；
封存后的处理只依赖所属报告与封存输入，报告删除才结束。各执行环节
（准备、问答、指标、模型）是注入的依赖条件，调度只安排调用与交接，
把各自产出经所属报告提供给使用方。
"""
import asyncio

from server import settings
from server.detection_errors import DetectionFlowError, DetectionInputError
from server.support.reporting.service import utc_now

# 采集中依赖的使用条件形状：{"accountId": ..., "mode": ...} 或 None。
COLLECTION_SECONDS = settings.CAPTURE_WINDOW_SECONDS

# 未分类失败在报告中同样只呈现固定公开文案，不外泄实现细节。
UNCLASSIFIED_FAILURE = "检测失败，请查看服务端日志"


class _Round:
    """一轮检测的调度状态；业务事实归报告，资源使用期归这里。"""

    def __init__(self, owner, kind, report_id):
        self.owner = owner
        self.kind = kind
        self.report_id = report_id
        self.media = None
        self.sealed_input = None
        self.measured = None
        self.prepare_task: asyncio.Task | None = None
        self.timer_task: asyncio.Task | None = None
        self.handoff_task: asyncio.Task | None = None
        self.sealed = False
        self.exists = True
        self.exists_stop = None

    def cancel_tasks(self):
        for task in (self.prepare_task, self.timer_task, self.handoff_task):
            if task is not None and not task.done():
                task.cancel()

    async def join_tasks(self):
        for task in (self.prepare_task, self.timer_task, self.handoff_task):
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)


def _usage_matches(round, usage):
    if usage is None:
        return False
    return usage.get("accountId") == round.owner and usage.get("mode") == round.kind


def create_detection_execution(reports, prepare, capture, questions, measure, analyze, clock):
    """装配检测调度；依赖端口由组合根注入，时钟可替换以便检验时序边界。"""
    rounds: dict[int, _Round] = {}

    class Execution:
        async def start(self, owner, mode):
            created = reports.create(owner, mode)
            round = _Round(owner, mode, created["id"])
            rounds[round.report_id] = round
            round.exists_stop = reports.observe_exists(
                owner, round.report_id,
                lambda exists: self._on_existence(round, exists),
            )
            try:
                if mode in ("health", "emotion"):
                    self._record(round, {"stage": "preparing"})
                    round.prepare_task = asyncio.create_task(prepare(owner, round.report_id, mode))
                    prepared = await round.prepare_task
                    round.prepare_task = None
                    round.media = capture.begin()
                    await questions.begin(round.report_id, prepared)
                else:
                    round.media = capture.begin()
                self._record(round, {"stage": "collecting", "collecting_since": utc_now()})
                round.timer_task = asyncio.create_task(self._collect(round))
            except BaseException:
                await self._abort(round, remove=True)
                raise
            return round.report_id

        async def set_usage(self, report_id, usage):
            """前端表达使用条件变化；封存前的失配工作由后端真实状态裁决。"""
            round = rounds.get(report_id)
            if round is None or round.sealed or _usage_matches(round, usage):
                return
            await self._abort(round, remove=True)

        async def finish(self, report_id):
            """到时前主动封存：先封存采集，再结清问答并交接测量与分析。"""
            round = rounds.get(report_id)
            if round is None:
                raise DetectionInputError("本轮检测不存在")
            if round.sealed:
                return
            await self._seal_and_hand_off(round)

        async def cancel(self, report_id):
            round = rounds.get(report_id)
            if round is None:
                raise DetectionInputError("本轮检测不存在")
            if round.sealed:
                raise DetectionInputError("本轮已封存，报告将保留并继续处理")
            await self._abort(round, remove=True)

        async def retry(self, report_id):
            """重试分析沿用本轮输入与已有结果，不重新采集或另建报告。"""
            round = rounds.get(report_id)
            if round is None or not round.sealed:
                raise DetectionInputError("本轮还没有可供分析的结果")
            if round.handoff_task is not None and not round.handoff_task.done():
                return
            self._record(round, {"stage": "measuring" if round.measured is None else "analyzing"})
            round.handoff_task = asyncio.create_task(self._process(round))

        async def question_displayed(self, report_id, index):
            if report_id in rounds:
                questions.displayed(report_id, index)

        # ------------------------------------------------------------------
        def _on_existence(self, round, exists):
            """报告被删除时结束依赖它的处理，不重建报告。"""
            if exists or rounds.get(round.report_id) is not round:
                return
            self._detach(round)

        def _detach(self, round):
            """同步解除调度登记、观察与未封存资源的使用期。"""
            rounds.pop(round.report_id, None)
            if round.exists_stop is not None:
                round.exists_stop()
                round.exists_stop = None
            round.cancel_tasks()
            if round.media is not None and not round.sealed:
                round.media.release()

        async def _collect(self, round):
            """采集窗口：到时封存属于时钟边界，不由页面或请求裁决。"""
            try:
                await clock.sleep(COLLECTION_SECONDS)
            except asyncio.CancelledError:
                return
            if rounds.get(round.report_id) is round and not round.sealed:
                await self._seal_and_hand_off(round)

        async def _seal_and_hand_off(self, round):
            round.sealed = True
            round.sealed_input = round.media.seal()
            if round.timer_task is not None and not round.timer_task.done():
                round.timer_task.cancel()
            self._record(round, {"stage": "measuring"})
            answers = await questions.finish(round.report_id)
            round.media.release()
            round.handoff_task = asyncio.create_task(self._process(round, answers))

        async def _process(self, round, answers=()):
            try:
                if round.measured is None:
                    measured = await measure(round.report_id, round.sealed_input, answers)
                    if not round.exists:
                        return
                    round.measured = measured
                    round.sealed_input = None
                self._record(round, {"stage": "analyzing"})
                facts = await analyze(round.report_id, round.measured)
                if not round.exists:
                    return
                if facts:
                    self._record(round, facts)
                self._record(round, {"stage": "complete"})
                rounds.pop(round.report_id, None)
            except DetectionFlowError as error:
                if round.exists:
                    self._record(round, {"stage": "failed", "failure": str(error)})
            except Exception:  # noqa: BLE001 - 未分类失败只记录固定公开文案
                if round.exists:
                    self._record(round, {"stage": "failed", "failure": UNCLASSIFIED_FAILURE})
            finally:
                round.media = None

        def _record(self, round, facts):
            if round.exists:
                reports.record(round.owner, round.report_id, facts)

        async def _abort(self, round, remove):
            self._detach(round)
            await round.join_tasks()
            await questions.cancel(round.report_id)
            if remove and round.exists:
                reports.remove(round.owner, round.report_id)

    return Execution()


class AsyncioClock:
    """生产时钟：与可控时钟同契约的 sleep 边界。"""

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


__all__ = ["AsyncioClock", "create_detection_execution"]
