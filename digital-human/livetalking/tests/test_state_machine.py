from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from config import parse_args
from state_machine import (
    ACTION_RANGES,
    IDLE_TYPES,
    TALK_RANGE,
    TRANSITION_RANGES,
    load_avatar_plan,
    setup_state_machine,
)


class FakeSession:
    def __init__(self, frame_count=6083):
        self.frame_count = frame_count
        self.custom_audiotype = 0
        self.custom_index = {}
        self.original_set_calls = []
        self.original_init_calls = 0

    def get_avatar_length(self):
        return self.frame_count

    def set_custom_state(self, state, reinit=True):
        self.original_set_calls.append((state, reinit))

    def init_customindex(self):
        self.original_init_calls += 1
        self.custom_audiotype = 0


class StateMachineTests(unittest.TestCase):
    def test_transition_file_is_loaded(self):
        self.assertEqual(57, len(TRANSITION_RANGES))
        self.assertIn((1, 4), TRANSITION_RANGES)
        self.assertIn((8, 9), TRANSITION_RANGES)

    def test_full_conversation_state_flow(self):
        session = FakeSession()
        setup_state_machine(session, hold_audio=True)
        session.init_customindex()

        initial_idle = session._current_state
        self.assertIn(initial_idle, IDLE_TYPES)
        self.assertTrue(session._hold_audio)
        self.assertEqual(initial_idle, session.custom_audiotype)

        session.set_custom_state(7)
        self.assertEqual(7, session._pending_action)
        self.assertEqual(initial_idle, session._current_state)

        session._on_state_cycle_complete(False)
        self.assertTrue(session._in_transition)
        self.assertEqual(7, session._transition_target)

        session._on_state_cycle_complete(False)
        self.assertFalse(session._in_transition)
        self.assertEqual(7, session._current_state)
        self.assertEqual(ACTION_RANGES[7], (session.action_start, session.action_end))

        session._speech_started = True
        session._on_state_cycle_complete(True)
        self.assertEqual(8, session._transition_target)
        session._on_state_cycle_complete(True)
        self.assertEqual(8, session._current_state)
        self.assertEqual(TALK_RANGE, (session.action_start, session.action_end))

        session._on_state_cycle_complete(False)
        self.assertTrue(session._talk_cycle_done)
        self.assertIn(session._transition_target, IDLE_TYPES)

    def test_http_mode_does_not_hold_audio(self):
        session = FakeSession()
        setup_state_machine(session)
        self.assertFalse(session._hold_audio)

    def test_speech_from_idle_jumps_to_talk(self):
        session = FakeSession()
        setup_state_machine(session)
        session.init_customindex()
        self.assertIn(session._current_state, IDLE_TYPES)
        # 无动作标签的闲聊：说话开始瞬间应直接过渡到 talk，而不是空转 idle
        session._speech_started = True
        self.assertTrue(session._go_talk_if_speaking())
        self.assertTrue(session._in_transition)
        self.assertEqual(8, session._transition_target)

    def test_talk_jump_is_refused_without_speech_or_with_pending_action(self):
        session = FakeSession()
        setup_state_machine(session)
        session.init_customindex()
        # 还没说话：不动
        self.assertFalse(session._go_talk_if_speaking())
        # 已排队动作：动作优先，不抢跳 talk
        session.set_custom_state(5)
        session._speech_started = True
        self.assertFalse(session._go_talk_if_speaking())
        # 空闲段播完（说话中）→ 先播动作
        session._on_state_cycle_complete(True)
        self.assertEqual(5, session._transition_target)
        # 动作过渡完成，进入动作段
        session._on_state_cycle_complete(True)
        self.assertEqual(5, session._current_state)
        # 动作播完时仍在说话 → talk
        session._on_state_cycle_complete(True)
        self.assertEqual(8, session._transition_target)

    def test_idle_cycle_completes_into_talk_while_speaking(self):
        session = FakeSession()
        setup_state_machine(session)
        session.init_customindex()
        session._on_state_cycle_complete(True)
        self.assertTrue(session._in_transition)
        self.assertEqual(8, session._transition_target)

    def test_short_avatar_fails_with_clear_error(self):
        with self.assertRaisesRegex(ValueError, "至少需要"):
            setup_state_machine(FakeSession(frame_count=100))

    def test_cosyvoice3_defaults_are_local(self):
        opt = parse_args([])
        self.assertEqual("cosyvoice3", opt.tts)
        self.assertIn("Fun-CosyVoice3", opt.cosyvoice3_model_dir)
        self.assertEqual("livetalking_fixed_voice", opt.TTS_SPK_ID)
        self.assertEqual("funasr-http", opt.asr_backend)
        self.assertEqual(12, opt.webrtc_video_queue_size)
        self.assertEqual(24, opt.webrtc_audio_queue_size)
        self.assertEqual(120.0, opt.webrtc_preroll_ms)
        # 2026-10-24：VAD 阈值三档实测——0.018 漏检弱语音→0.009 噪声开轮太灵敏
        # →0.012 折中；启动窗 2026-10-25 提到 250ms（环境音筛选，需持续发声才开轮）
        self.assertEqual(0.012, opt.mic_vad_threshold)
        self.assertEqual(250, opt.mic_vad_start_ms)
        # 2026-10-24 提速：静音判停与话尾宽限连续调优（650→400 静音、450 宽限）
        # 2026-10-25 再提速（用户反馈"第一句话慢"）：320 静音、250 宽限
        self.assertEqual(320, opt.mic_vad_silence_ms)
        self.assertEqual(250, opt.mic_reopen_grace_ms)
        self.assertEqual(20.0, opt.mic_max_utterance_s)
        self.assertEqual(24, opt.webrtc_audio_queue_size)
        self.assertEqual(120.0, opt.webrtc_preroll_ms)
        self.assertEqual(32, opt.initial_preload_frames)


class AvatarPlanTests(unittest.TestCase):
    YY_SEGMENTS = {
        "idle1": {"range": [0, 183], "frames": 184},
        "idle_to_comfort": {"range": [184, 274], "frames": 91},
        "comfort": {"range": [275, 442], "frames": 168},
        "comfort_to_talk": {"range": [443, 533], "frames": 91},
        "talk": {"range": [534, 801], "frames": 268},
        "talk_to_idle": {"range": [802, 892], "frames": 91},
        "idle1_final": {"range": [893, 1076], "frames": 184},
        "_total": 1077,
        "_fps": 30,
    }

    def _plan_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "ranges.json"
        path.write_text(json.dumps(self.YY_SEGMENTS), encoding="utf-8")
        return path

    def test_missing_file_returns_none(self):
        self.assertIsNone(load_avatar_plan("data/definitely_missing.json"))

    def test_parse_yy_segment_schema(self):
        plan = load_avatar_plan(self._plan_file())
        self.assertIsNotNone(plan)
        # idle1 与 idle1_final 是两段待机（同一姿势的循环拷贝）
        self.assertEqual([(0, 183), (893, 1076)], plan["idle_ranges"])
        self.assertEqual([1, 2], plan["idle_types"])
        self.assertEqual({7: (275, 442)}, plan["action_ranges"])
        self.assertEqual((534, 801), plan["talk"])
        self.assertEqual((534, 801), plan["talk2"])  # 无 talk2 时复用 talk
        # 过渡泛化：一条 idle→comfort 扩到两段 idle；talk→idle 同样扩到两段
        self.assertEqual((184, 274), plan["transitions"][(1, 7)])
        self.assertEqual((184, 274), plan["transitions"][(2, 7)])
        self.assertEqual((443, 533), plan["transitions"][(7, 8)])
        self.assertEqual((443, 533), plan["transitions"][(7, 9)])
        self.assertEqual((802, 892), plan["transitions"][(8, 1)])
        self.assertEqual((802, 892), plan["transitions"][(8, 2)])
        self.assertEqual((802, 892), plan["transitions"][(9, 2)])

    def test_plan_drives_state_machine(self):
        plan = load_avatar_plan(self._plan_file())
        session = FakeSession(frame_count=1077)
        setup_state_machine(session, plan=plan)
        session.init_customindex()
        self.assertIn(session._current_state, (1, 2))

        # comfort 动作：先排队，待机段播完走过渡
        session.set_custom_state(7)
        self.assertEqual(7, session._pending_action)
        session._on_state_cycle_complete(False)
        self.assertTrue(session._in_transition)
        self.assertEqual(7, session._transition_target)
        self.assertEqual((184, 274), (session.action_start, session.action_end))
        session._on_state_cycle_complete(False)
        self.assertEqual(7, session._current_state)
        self.assertEqual((275, 442), (session.action_start, session.action_end))

        # 动作播完仍在说话 → 过渡进 talk
        session._speech_started = True
        session._on_state_cycle_complete(True)
        self.assertEqual(8, session._transition_target)
        session._on_state_cycle_complete(True)
        self.assertEqual(8, session._current_state)
        self.assertEqual((534, 801), (session.action_start, session.action_end))

        # 说完回待机（泛化出的 talk→idle2 也存在）
        session._on_state_cycle_complete(False)
        self.assertIn(session._transition_target, (1, 2))

    def test_plan_without_idle_or_talk_is_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "ranges.json"
        path.write_text(json.dumps({"_total": 10, "wave": {"range": [0, 9]}}),
                        encoding="utf-8")
        self.assertIsNone(load_avatar_plan(path))

    def test_unsupported_action_is_ignored_not_raised(self):
        """yy 段表只有 comfort：greet/encourage/praise 必须静默跳过。

        改前 range_for() 直接抛 ValueError，llm.py 每个 delta 重试一次，
        一轮回复能刷出二十多条 ERROR + traceback。
        """
        plan = load_avatar_plan(self._plan_file())
        session = FakeSession(frame_count=1077)
        setup_state_machine(session, plan=plan)
        session.init_customindex()

        self.assertTrue(session._supports_state(7))
        self.assertTrue(session._supports_state(1))
        for unsupported in (4, 5, 6):
            self.assertFalse(session._supports_state(unsupported))

        before = session._current_state
        calls_before = len(session.original_set_calls)
        self.assertIs(False, session.set_custom_state(4))  # greet 不在段表
        self.assertEqual(before, session._current_state)
        self.assertIsNone(session._pending_action)
        self.assertEqual(
            calls_before, len(session.original_set_calls), "不应改动渲染状态"
        )

        # 段表里有的 comfort 照常工作
        session.set_custom_state(7)
        self.assertEqual(7, session._pending_action)




class DanceSegmentTests(unittest.TestCase):
    """dance 段（dance/ 目录视频构建）进段表后的状态机行为。

    页面"跳一段舞"按钮走 /api/dance：同步预加载后 _activate_state(10)
    立即开播（不等待机段播完），跳完自动走 dance_to_idle 过渡回待机。
    """

    DANCE_SEGMENTS = dict(AvatarPlanTests.YY_SEGMENTS)
    DANCE_SEGMENTS.update({
        "dance": {"range": [1077, 1225], "frames": 149},
        # 回场过渡复用 talk_to_idle 的帧（gen_dance.py 的做法）
        "dance_to_idle": {"range": [802, 892], "frames": 91},
        "_total": 1226,
    })

    def _plan(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "ranges.json"
        path.write_text(json.dumps(self.DANCE_SEGMENTS), encoding="utf-8")
        return load_avatar_plan(path)

    def test_dance_parses_as_action_state_10(self):
        plan = self._plan()
        self.assertIsNotNone(plan)
        self.assertEqual((1077, 1225), plan["action_ranges"].get(10))
        self.assertEqual({7, 10}, set(plan["action_ranges"]))
        # dance_to_idle 过渡被解析并泛化到两段待机
        self.assertEqual((802, 892), plan["transitions"][(10, 1)])
        self.assertEqual((802, 892), plan["transitions"][(10, 2)])

    def test_activate_dance_plays_then_transitions_back_to_idle(self):
        session = FakeSession(frame_count=1226)
        setup_state_machine(session, plan=self._plan())
        session.init_customindex()

        # /api/dance 的路径：预加载 + 直接 activate（不排队等待机段播完）
        self.assertTrue(hasattr(session, "_preload_state"))
        session._preload_state(10, background=False)
        session._activate_state(10)
        self.assertEqual(10, session._current_state)
        self.assertEqual((1077, 1225), (session.action_start, session.action_end))

        # 舞蹈播完（静音）→ 走 dance_to_idle 过渡 → 回到待机
        session._on_state_cycle_complete(False)
        self.assertTrue(session._in_transition)
        self.assertIn(session._transition_target, (1, 2))
        self.assertEqual((802, 892), (session.action_start, session.action_end))
        session._on_state_cycle_complete(False)
        self.assertIn(session._current_state, (1, 2))
        self.assertFalse(session._in_transition)

    def test_set_state_still_queues_dance_like_other_actions(self):
        session = FakeSession(frame_count=1226)
        setup_state_machine(session, plan=self._plan())
        session.init_customindex()
        session.set_custom_state(10)
        self.assertEqual(10, session._pending_action)


if __name__ == "__main__":
    unittest.main()
