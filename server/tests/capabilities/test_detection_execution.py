"""只检验后端检测调度；准备、问答、采集、计算及记录各为依赖条件。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, sentinel

import pytest

from server.tests.capabilities.bindings import capability
from server.tests.capabilities.conditions import Clock, Signal, settle


class Fixture:
    def __init__(self):
        self.clock = Clock()
        self.events = []
        self.existence = Signal(True)
        self.reports = SimpleNamespace(
            create=Mock(side_effect=self.create), record=Mock(), remove=Mock(),
            observe_exists=lambda owner, report_id, listener: self.existence.observe(listener),
        )
        self.prepare = AsyncMock(return_value=sentinel.prepared)
        self.media = SimpleNamespace(
            seal=Mock(side_effect=lambda: self.event("seal", sentinel.sealed)), release=Mock(),
        )
        self.capture = SimpleNamespace(
            begin=Mock(side_effect=lambda: self.event("capture", self.media)),
        )
        self.questions = SimpleNamespace(
            begin=AsyncMock(), displayed=Mock(), finish=AsyncMock(return_value=sentinel.answers),
            cancel=AsyncMock(),
        )
        self.measure = AsyncMock(return_value=sentinel.measured)
        self.analyze = AsyncMock(return_value={"report": {"text": "model output"}})
        self.service = capability(
            "detection", reports=self.reports, prepare=self.prepare, capture=self.capture,
            questions=self.questions, measure=self.measure, analyze=self.analyze, clock=self.clock,
        )

    def event(self, name, result=None):
        self.events.append(name)
        return result

    def create(self, owner, mode):
        return self.event("report", {"id": 31 + self.reports.create.call_count})

    async def start(self, mode="health", owner=1):
        return await self.service.start(owner, mode)


@pytest.fixture
def scenario():
    def run(case):
        async def main():
            f = Fixture()
            try:
                await case(f)
            finally:
                tasks = asyncio.all_tasks() - {asyncio.current_task()}
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run(main())
    return run


@pytest.mark.parametrize("mode", ["health", "emotion", "fatigue"])
def test_start_creates_owned_report_before_preparation_and_acquisition(scenario, mode):
    async def case(f):
        async def prepare(owner, report_id, kind):
            assert f.events == ["report"]
            assert owner == 1 and kind == mode
            return sentinel.prepared
        f.prepare.side_effect = prepare
        report_id = await f.start(mode)
        await settle()
        assert f.events.index("report") < f.events.index("capture")
        f.reports.create.assert_called_with(1, mode)
        if mode == "fatigue":
            f.prepare.assert_not_awaited()
            f.questions.begin.assert_not_awaited()
        else:
            f.prepare.assert_awaited_with(1, report_id, mode)
            f.questions.begin.assert_awaited_with(report_id, sentinel.prepared)
    scenario(case)


def test_report_creation_failure_never_acquires_device_or_prepares(scenario):
    async def case(f):
        f.reports.create.side_effect = RuntimeError("unavailable")
        with pytest.raises(RuntimeError, match="unavailable"):
            await f.start()
        f.prepare.assert_not_awaited()
        f.capture.begin.assert_not_called()
    scenario(case)


def test_preparation_must_complete_before_acquisition(scenario):
    async def case(f):
        ready = asyncio.Event()
        async def prepare(*args):
            await ready.wait()
            return sentinel.prepared
        f.prepare.side_effect = prepare
        starting = asyncio.create_task(f.start())
        await settle()
        f.capture.begin.assert_not_called()
        ready.set()
        await starting
        await settle()
        f.capture.begin.assert_called()
    scenario(case)


def test_usage_loss_during_preparation_stops_it_before_acquisition(scenario):
    async def case(f):
        cancelled = asyncio.Event()
        async def prepare(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        f.prepare.side_effect = prepare
        starting = asyncio.create_task(f.start())
        await settle()
        # 编号来自报告端口，本例报告当前事实由依赖条件提供。
        await f.service.set_usage(32, None)
        await settle()
        assert cancelled.is_set()
        f.capture.begin.assert_not_called()
        f.reports.remove.assert_called_with(1, 32)
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)
    scenario(case)


def test_clock_owns_the_sixty_second_sealing_boundary(scenario):
    async def case(f):
        await f.start("fatigue")
        await f.clock.advance(59)
        f.media.seal.assert_not_called()
        await f.clock.advance(1)
        f.media.seal.assert_called()
    scenario(case)


def test_early_finish_seals_then_joins_answers_before_handing_off_to_measurement(scenario):
    async def case(f):
        done = asyncio.Event()
        async def finish_answers(report_id):
            assert "seal" in f.events
            await done.wait()
            return sentinel.answers
        f.questions.finish.side_effect = finish_answers
        report_id = await f.start()
        finishing = asyncio.create_task(f.service.finish(report_id))
        await settle()
        f.measure.assert_not_awaited()
        done.set()
        await finishing
        await settle()
        f.measure.assert_awaited_with(report_id, sentinel.sealed, sentinel.answers)
        f.media.release.assert_called()
    scenario(case)


def test_sealed_input_is_automatically_handed_to_measurement_then_analysis(scenario):
    async def case(f):
        report_id = await f.start()
        await f.service.finish(report_id)
        await settle()
        f.measure.assert_awaited_with(report_id, sentinel.sealed, sentinel.answers)
        f.analyze.assert_awaited_with(report_id, sentinel.measured)
        f.reports.record.assert_any_call(1, report_id, {"report": {"text": "model output"}})
    scenario(case)


@pytest.mark.parametrize("usage", [None, {"accountId": 2, "mode": "health"}, {"accountId": 1, "mode": "emotion"}])
def test_usage_loss_cancels_unsealed_work_and_removes_only_its_report(scenario, usage):
    async def case(f):
        report_id = await f.start()
        await f.service.set_usage(report_id, usage)
        await settle()
        f.questions.cancel.assert_awaited_with(report_id)
        f.media.release.assert_called()
        f.reports.remove.assert_called_with(1, report_id)
        f.analyze.assert_not_awaited()
    scenario(case)


def test_explicit_cancel_ends_acquisition_and_question_usage(scenario):
    async def case(f):
        report_id = await f.start()
        await f.service.cancel(report_id)
        f.questions.cancel.assert_awaited_with(report_id)
        f.media.release.assert_called()
        f.reports.remove.assert_called_with(1, report_id)
    scenario(case)


def test_sealed_processing_does_not_depend_on_page_or_login_usage(scenario):
    async def case(f):
        entered, done = asyncio.Event(), asyncio.Event()
        async def analyze(*args):
            entered.set()
            await done.wait()
            return {"report": {"text": "done"}}
        f.analyze.side_effect = analyze
        report_id = await f.start()
        await f.service.finish(report_id)
        await settle()
        assert entered.is_set()
        await f.service.set_usage(report_id, None)
        f.reports.remove.assert_not_called()
        done.set()
        await settle()
        f.reports.record.assert_any_call(1, report_id, {"report": {"text": "done"}})
    scenario(case)


def test_report_deletion_ends_processing_instead_of_recreating_report(scenario):
    async def case(f):
        cancelled = asyncio.Event()
        async def analyze(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        f.analyze.side_effect = analyze
        report_id = await f.start()
        await f.service.finish(report_id)
        await settle()
        before = list(f.reports.record.call_args_list)
        f.existence.publish(False)
        await settle()
        assert cancelled.is_set()
        assert f.reports.record.call_args_list == before
        assert f.reports.create.call_count == 1
    scenario(case)


def test_failed_analysis_retry_reuses_measurement_and_original_target(scenario):
    async def case(f):
        f.analyze.side_effect = [RuntimeError("model unavailable"), {"report": {"text": "retried"}}]
        report_id = await f.start()
        await f.service.finish(report_id)
        await settle()
        await f.service.retry(report_id)
        await settle()
        f.measure.assert_awaited_once()
        assert f.capture.begin.call_count == 1
        assert f.reports.create.call_count == 1
        assert all(call.args == (report_id, sentinel.measured) for call in f.analyze.await_args_list)
        f.reports.record.assert_any_call(1, report_id, {"report": {"text": "retried"}})
    scenario(case)


def test_analysis_lifetime_does_not_retain_acquisition_or_redirect_old_output(scenario):
    async def case(f):
        done = asyncio.Event()
        async def analyze(report_id, measured):
            await done.wait()
            return {"report": {"text": str(report_id)}}
        f.analyze.side_effect = analyze
        first = await f.start()
        await f.service.finish(first)
        await settle()
        f.media.release.assert_called()
        second = await f.start("emotion")
        assert first != second
        done.set()
        await settle()
        f.reports.record.assert_any_call(1, first, {"report": {"text": str(first)}})
    scenario(case)


def test_display_feedback_is_forwarded_to_the_question_owner(scenario):
    async def case(f):
        report_id = await f.start()
        await f.service.question_displayed(report_id, 2)
        f.questions.displayed.assert_called_with(report_id, 2)
    scenario(case)
