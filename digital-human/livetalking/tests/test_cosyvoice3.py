from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

import tts.cosyvoice3 as cv3
from tts.cosyvoice3 import CosyVoice3TTS

# 模型自带分词器对 "You are a helpful assistant.<|endofprompt|>" 的结果，
# 与 spk2info.pt 里已注册音色的前缀逐 token 一致。
PREFIX_IDS = [2610, 525, 264, 10950, 17847, 13, 151646]
END_OF_PROMPT = 151646


class FakeParent:
    def __init__(self):
        self.frames = []

    def put_audio_frame(self, frame, event):
        self.frames.append((frame.copy(), dict(event)))


def entry(with_prefix: bool) -> dict:
    ids = (PREFIX_IDS if with_prefix else []) + [100, 101, 102]
    prompt_text = torch.tensor([ids], dtype=torch.int32)
    return {
        "prompt_text": prompt_text,
        "prompt_text_len": torch.tensor([prompt_text.shape[1]], dtype=torch.int32),
        "llm_prompt_speech_token": torch.zeros(1, 5, dtype=torch.int32),
    }


class FakeFrontend:
    def __init__(self, speakers=None, with_tokenizer=True):
        self.spk2info = dict(speakers or {})
        if with_tokenizer:
            self._extract_text_token = lambda text: (
                torch.tensor([PREFIX_IDS], dtype=torch.int32),
                torch.tensor([len(PREFIX_IDS)], dtype=torch.int32),
            )


class FakeCosyVoiceModel:
    """Only the surface the TTS module actually touches."""

    def __init__(self, speakers=None, sample_rate=16000, with_tokenizer=True):
        self.frontend = FakeFrontend(speakers, with_tokenizer)
        self.model_dir = "models/Fun-CosyVoice3-0.5B"
        self.sample_rate = sample_rate
        self.saved = 0
        self.requested_spk = None

    def list_available_spks(self):
        return list(self.frontend.spk2info.keys())

    def save_spkinfo(self):
        self.saved += 1

    def inference_zero_shot(
        self, text, a, b, zero_shot_spk_id=None, stream=True, speed=1.0
    ):
        self.requested_spk = zero_shot_spk_id
        yield {"tts_speech": torch.zeros(1, 8, dtype=torch.float32)}


class CosyVoice3Tests(unittest.TestCase):
    def test_stream_keeps_remainder_and_marks_boundaries(self):
        parent = FakeParent()
        tts = CosyVoice3TTS(SimpleNamespace(fps=25), parent)
        samples = np.linspace(-0.5, 0.5, 650, dtype=np.float32)

        tts.stream_tts(iter([samples[:300], samples[300:]]), ("你好", {"id": 1}))

        self.assertEqual(3, len(parent.frames))
        self.assertEqual((320,), parent.frames[0][0].shape)
        self.assertEqual("start", parent.frames[0][1]["status"])
        self.assertNotIn("status", parent.frames[1][1])
        self.assertEqual("end", parent.frames[2][1]["status"])
        np.testing.assert_allclose(parent.frames[0][0], samples[:320])
        np.testing.assert_allclose(parent.frames[1][0], samples[320:640])
        np.testing.assert_allclose(parent.frames[2][0][:10], samples[640:])
        np.testing.assert_allclose(parent.frames[2][0][10:], 0)


class LlmJobGuardTests(unittest.TestCase):
    """llm_job 崩溃时上游流式循环必须能退出（HANDOFF P0-A）。"""

    class Boom(Exception):
        pass

    def test_crash_still_marks_the_stream_finished(self):
        model = SimpleNamespace(llm_end_dict={})

        def broken_llm_job(text, prompt_text, speech_token, embedding, uuid):
            raise self.Boom("assertion in llm")

        model.llm_job = broken_llm_job
        cv3._install_llm_job_guard(model)

        with self.assertRaises(self.Boom):
            model.llm_job("text", None, None, None, "uuid-1")

        self.assertIs(True, model.llm_end_dict["uuid-1"])

    def test_guard_is_idempotent(self):
        model = SimpleNamespace(llm_end_dict={}, llm_job=lambda *a: None)

        cv3._install_llm_job_guard(model)
        guarded = model.llm_job
        cv3._install_llm_job_guard(model)

        self.assertIs(guarded, model.llm_job)

    def test_guard_targets_the_inner_model_of_the_facade(self):
        """AutoModel 返回 CosyVoice3 外壳，llm_job 在内层 .model 上。"""
        inner = SimpleNamespace(llm_end_dict={})

        def broken(*args):
            raise self.Boom("assertion in llm")

        inner.llm_job = broken
        facade = SimpleNamespace(model=inner, frontend=SimpleNamespace(spk2info={}))

        self.assertTrue(cv3._install_llm_job_guard(facade))
        self.assertIs(inner, cv3._llm_job_owner(facade))
        with self.assertRaises(self.Boom):
            inner.llm_job("t", None, None, None, "uuid-2")
        self.assertIs(True, inner.llm_end_dict["uuid-2"])

    def test_guard_reports_false_when_no_owner_exists(self):
        self.assertFalse(cv3._install_llm_job_guard(SimpleNamespace()))


class LiveRefSpeakerTests(unittest.TestCase):
    """实时口型音色以参考音频为准，用当前模型注册（码本版本才一致）。"""

    REF_TEXT = "这是我们发明的智能情绪探测器"

    def _fake_model(self):
        model = FakeCosyVoiceModel()
        model.added = []

        def add_zero_shot_spk(prompt_text, wav, spk_id):
            model.added.append((prompt_text, wav, spk_id))
            model.frontend.spk2info[spk_id] = entry(with_prefix=True)

        model.add_zero_shot_spk = add_zero_shot_spk
        return model

    def _opt(self, ref: Path, text: str = REF_TEXT):
        return SimpleNamespace(
            live_spk_id="live_spk", live_ref_file=str(ref), live_ref_text=text
        )

    def test_registers_from_reference_audio_then_reuses_it(self):
        with TemporaryDirectory() as tmp:
            ref = Path(tmp) / "live_ref.wav"
            ref.write_bytes(b"fake wav payload")
            markers = Path(tmp) / "markers"
            model = self._fake_model()

            with patch.object(cv3, "LIVE_SPK_MARKER_DIR", markers):
                first = cv3._ensure_live_ref_speaker(
                    model, "models/Fun-CosyVoice3-0.5B", self._opt(ref)
                )
                second = cv3._ensure_live_ref_speaker(
                    model, "models/Fun-CosyVoice3-0.5B", self._opt(ref)
                )

            self.assertEqual("live_spk", first)
            self.assertEqual("live_spk", second)
            self.assertEqual(1, len(model.added))
            self.assertEqual(1, model.saved)
            prompt_text, wav, spk_id = model.added[0]
            self.assertEqual("live_spk", spk_id)
            self.assertEqual(str(ref), wav)
            self.assertTrue(prompt_text.endswith(self.REF_TEXT))
            self.assertTrue(prompt_text.startswith(cv3.COSYVOICE3_PROMPT_PREFIX))
            self.assertIn("live_spk", model.frontend.spk2info)
            self.assertTrue((markers / "live_spk.json").is_file())

    def test_changed_audio_triggers_reregistration(self):
        with TemporaryDirectory() as tmp:
            ref = Path(tmp) / "live_ref.wav"
            ref.write_bytes(b"first take")
            markers = Path(tmp) / "markers"
            model = self._fake_model()

            with patch.object(cv3, "LIVE_SPK_MARKER_DIR", markers):
                cv3._ensure_live_ref_speaker(
                    model, "models/Fun-CosyVoice3-0.5B", self._opt(ref)
                )
                ref.write_bytes(b"second take, different bytes")
                cv3._ensure_live_ref_speaker(
                    model, "models/Fun-CosyVoice3-0.5B", self._opt(ref)
                )

            self.assertEqual(2, len(model.added))
            self.assertEqual(2, model.saved)

    def test_changed_text_triggers_reregistration(self):
        with TemporaryDirectory() as tmp:
            ref = Path(tmp) / "live_ref.wav"
            ref.write_bytes(b"same audio")
            markers = Path(tmp) / "markers"
            model = self._fake_model()

            with patch.object(cv3, "LIVE_SPK_MARKER_DIR", markers):
                cv3._ensure_live_ref_speaker(
                    model, "models/Fun-CosyVoice3-0.5B", self._opt(ref)
                )
                cv3._ensure_live_ref_speaker(
                    model, "models/Fun-CosyVoice3-0.5B", self._opt(ref, "改过的文本")
                )

            self.assertEqual(2, len(model.added))
            self.assertEqual("改过的文本", model.added[1][0].removeprefix(
                cv3.COSYVOICE3_PROMPT_PREFIX
            ))

    def test_missing_reference_audio_is_skipped(self):
        with TemporaryDirectory() as tmp:
            model = self._fake_model()
            result = cv3._ensure_live_ref_speaker(
                model,
                "models/Fun-CosyVoice3-0.5B",
                self._opt(Path(tmp) / "absent.wav"),
            )
        self.assertIsNone(result)
        self.assertEqual([], model.added)

    def test_missing_reference_text_is_skipped(self):
        with TemporaryDirectory() as tmp:
            ref = Path(tmp) / "live_ref.wav"
            ref.write_bytes(b"audio without transcript")
            model = self._fake_model()
            result = cv3._ensure_live_ref_speaker(
                model,
                "models/Fun-CosyVoice3-0.5B",
                self._opt(ref, "   "),
            )
        self.assertIsNone(result)
        self.assertEqual([], model.added)


class LiveSpeakerTests(unittest.TestCase):
    """旧缓存搬运只是回退路径，且必须先补 <|endofprompt|> 前缀。"""

    def _write_cache(self, path: Path, spk_id: str, with_prefix: bool) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save({spk_id: entry(with_prefix)}, path)
        return path

    def test_imports_and_repairs_speaker_from_avatar_cache(self):
        with TemporaryDirectory() as tmp:
            cache = self._write_cache(Path(tmp) / "spk2info.pt", "live_spk", False)
            model = FakeCosyVoiceModel()

            result = cv3._import_live_speaker(model, "live_spk", cache_path=str(cache))

            self.assertEqual("live_spk", result)
            imported = model.frontend.spk2info["live_spk"]
            self.assertTrue(cv3._has_endofprompt(imported))
            self.assertEqual(3 + len(PREFIX_IDS), imported["prompt_text"].shape[1])
            self.assertEqual(
                imported["prompt_text"].shape[1],
                int(imported["prompt_text_len"].reshape(-1)[0]),
            )
            self.assertEqual(1, model.saved)

    def test_import_keeps_an_already_valid_cache_untouched(self):
        with TemporaryDirectory() as tmp:
            cache = self._write_cache(Path(tmp) / "spk2info.pt", "live_spk", True)
            model = FakeCosyVoiceModel()

            result = cv3._import_live_speaker(model, "live_spk", cache_path=str(cache))

            self.assertEqual("live_spk", result)
            self.assertTrue(cv3._has_endofprompt(model.frontend.spk2info["live_spk"]))

    def test_existing_broken_entry_is_repaired_in_place(self):
        model = FakeCosyVoiceModel({"live_spk": entry(with_prefix=False)})

        result = cv3._import_live_speaker(model, "live_spk", cache_path="unused.pt")

        self.assertEqual("live_spk", result)
        self.assertTrue(cv3._has_endofprompt(model.frontend.spk2info["live_spk"]))
        self.assertEqual(1, model.saved)

    def test_existing_valid_entry_is_reused_without_writing(self):
        model = FakeCosyVoiceModel({"live_spk": entry(with_prefix=True)})

        with patch.object(torch, "load", side_effect=AssertionError("不应读取缓存")):
            result = cv3._import_live_speaker(model, "live_spk", cache_path="unused.pt")

        self.assertEqual("live_spk", result)
        self.assertEqual(0, model.saved)

    def test_unrepairable_entry_is_refused(self):
        with TemporaryDirectory() as tmp:
            cache = self._write_cache(Path(tmp) / "spk2info.pt", "live_spk", False)
            model = FakeCosyVoiceModel(with_tokenizer=False)

            result = cv3._import_live_speaker(model, "live_spk", cache_path=str(cache))

            self.assertIsNone(result)
            self.assertNotIn("live_spk", model.frontend.spk2info)

    def test_missing_cache_reports_none(self):
        model = FakeCosyVoiceModel()

        with TemporaryDirectory() as tmp:
            result = cv3._import_live_speaker(
                model, "live_spk", cache_path=str(Path(tmp) / "absent.pt")
            )

        self.assertIsNone(result)
        self.assertEqual({}, model.frontend.spk2info)

    def test_cache_with_other_speakers_reports_none(self):
        with TemporaryDirectory() as tmp:
            cache = self._write_cache(Path(tmp) / "spk2info.pt", "someone_else", True)

            result = cv3._import_live_speaker(
                FakeCosyVoiceModel(), "live_spk", cache_path=str(cache)
            )

        self.assertIsNone(result)

    def test_cache_candidates_follow_avatar_dir_and_override(self):
        default = cv3._avatar_spk_cache_candidates("my_avatar")
        self.assertEqual(1, len(default))
        self.assertEqual(
            Path("data/avatars/my_avatar/spk2info.pt"),
            default[0].relative_to(cv3.ROOT),
        )

        overridden = cv3._avatar_spk_cache_candidates("my_avatar", "data/voices/x.pt")
        self.assertEqual(
            Path("data/voices/x.pt"), overridden[0].relative_to(cv3.ROOT)
        )
        self.assertEqual([], cv3._avatar_spk_cache_candidates(""))

    def test_live_mode_speaks_with_trained_speaker(self):
        opt = SimpleNamespace(
            fps=25, live_spk_id="live_spk", avatar_id="my_avatar", live_spk_cache=""
        )
        tts = CosyVoice3TTS(opt, FakeParent())
        model = FakeCosyVoiceModel({"live_spk": entry(with_prefix=True)})

        # 界面上持久化的音色选择（data/voices/_active.json）不得影响测试：
        # 指到不存在的路径，等价于"未选择投递音色"
        from server import assets as asset_registry
        with patch.object(cv3, "_load_model", return_value=model), patch.object(
            cv3, "_active_persona", return_value=None
        ), patch.object(
            asset_registry, "ACTIVE_VOICE_FILE", cv3.ROOT / "_no_such_active.json"
        ), patch.object(
            cv3, "_ensure_live_ref_speaker", return_value="live_spk"
        ), patch.object(
            cv3,
            "_ensure_guide_boy_speaker",
            side_effect=AssertionError("实时口型不应回退暖暖音色"),
        ):
            list(tts._generate("你好"))

        self.assertEqual("live_spk", model.requested_spk)

    def test_default_page_mode_uses_nuannuan_yunxia_voice(self):
        # 暖暖（轻量页/导览页）唯一音色 = 云霞卡通 guide_boy_v1
        # （2026-10-24 用户拍板：旧默认页男孩音色 v5 已删除）
        tts = CosyVoice3TTS(SimpleNamespace(fps=25), FakeParent(), default_page_voice=True)
        model = FakeCosyVoiceModel()

        with patch.object(cv3, "_load_model", return_value=model), patch.object(
            cv3,
            "_ensure_guide_boy_speaker",
            return_value="guide_boy_v1",
        ), patch.object(
            cv3,
            "_live_speaker_id",
            side_effect=AssertionError("轻量页不应触碰实时口型音色"),
        ):
            list(tts._generate("你好"))

        self.assertEqual("guide_boy_v1", model.requested_spk)


if __name__ == "__main__":
    unittest.main()
