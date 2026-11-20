"""既有逐题规则上提到后端问答职责，测试不再驱动前端时钟。

沿用当前已确认的回答策略：展示后 3 秒首次转写，空结果后 2 秒再取，
有文字后等待 2 秒收尾，单题最多 15 秒。采集 60 秒由采集能力另行保证。
"""
import importlib
from pathlib import Path


def timing():
    entry = Path(__file__).resolve().parents[2] / "analysis/questions.py"
    assert entry.is_file(), "待实现：将原逐题计时与回答判定移至所属后端问答能力"
    module = importlib.import_module("server.analysis.questions")
    factory = getattr(module, "QuestionTiming", None)
    assert callable(factory), "待实现：可用单调时间检验的逐题策略"
    return factory()


def test_clock_without_display_confirmation_does_not_start_a_question():
    policy = timing()
    assert policy.next_action(60_000) is None


def test_first_transcription_and_empty_retry_keep_the_existing_intervals():
    policy = timing()
    policy.start(1_000)
    assert policy.next_action(3_999) is None
    assert policy.next_action(4_000) == "transcribe"
    policy.transcribed("", 4_000)
    assert policy.next_action(5_999) is None
    assert policy.next_action(6_000) == "transcribe"


def test_transcript_waits_for_the_existing_answer_settlement_interval():
    policy = timing()
    policy.start(1_000)
    assert policy.next_action(4_000) == "transcribe"
    policy.transcribed("今天感觉很好", 4_000)
    assert policy.next_action(5_999) is None
    assert policy.next_action(6_000) == "close"


def test_a_question_without_answer_closes_at_the_existing_fifteen_second_limit():
    policy = timing()
    policy.start(1_000)
    assert policy.next_action(16_000) == "close"


def test_the_next_question_does_not_inherit_the_previous_confirmation_time():
    policy = timing()
    policy.start(1_000)
    policy.transcribed("上一题回答", 4_000)
    policy.start(20_000)
    assert policy.next_action(22_000) is None
    assert policy.next_action(23_000) == "transcribe"
