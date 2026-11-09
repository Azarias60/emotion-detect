"""人物/音色素材库：黑名单忽略、列表、音色激活、投递视频与生成校验。

全部离线（不加载模型、不起子进程），目录全部指向临时文件夹。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from server import assets


class AssetsTestBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.avatars = root / "avatars"
        self.drop = root / "drop"
        self.voices = root / "voices"
        for d in (self.avatars, self.drop, self.voices):
            d.mkdir(parents=True)
        patches = [
            mock.patch.object(assets, "AVATARS_DIR", self.avatars),
            mock.patch.object(assets, "DROP_DIR", self.drop),
            mock.patch.object(assets, "VOICES_DIR", self.voices),
            mock.patch.object(assets, "GEN_LOG_DIR", self.drop / "_gen"),
            mock.patch.object(assets, "ACTIVE_VOICE_FILE", self.voices / "_active.json"),
            mock.patch.object(assets, "ROOT", root),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        assets.init(SimpleNamespace(avatar_id="my_avatar"))


def make_avatar(base: Path, name: str, frames: int = 2000) -> None:
    avatar = base / name
    (avatar / "full_imgs").mkdir(parents=True)
    (avatar / "face_imgs").mkdir(parents=True)
    for i in range(frames):
        (avatar / "full_imgs" / f"{i:08d}.png").write_bytes(b"x")
        (avatar / "face_imgs" / f"{i:08d}.png").write_bytes(b"x")
    (avatar / "coords.pkl").write_bytes(b"x")


class AvatarListingTests(AssetsTestBase):
    def test_legacy_zzf1_hidden_but_yy_listed(self):
        # yy（原 zzf2 素材）可选；更早遗留的 zzf1 仍被忽略
        make_avatar(self.avatars, "my_avatar")
        make_avatar(self.avatars, "yy", frames=1077)
        make_avatar(self.avatars, "zzf1")
        ids = [item["id"] for item in assets.list_avatars()]
        self.assertEqual(["my_avatar", "yy"], ids)

    def test_partial_avatars_are_not_ready(self):
        (self.avatars / "half" / "full_imgs").mkdir(parents=True)
        (self.avatars / "half" / "full_imgs" / "00000000.png").write_bytes(b"x")
        self.assertEqual([], assets.list_avatars())

    def test_is_allowed_avatar(self):
        self.assertTrue(assets.is_allowed_avatar("my_avatar"))
        self.assertTrue(assets.is_allowed_avatar("yy"))
        self.assertFalse(assets.is_allowed_avatar("zzf1"))
        self.assertFalse(assets.is_allowed_avatar("不合法名字"))
        self.assertFalse(assets.is_allowed_avatar(""))


def write_segment_table(
    avatar_dir: Path, actions: tuple[str, ...] = ("comfort",)
) -> None:
    """给形象写一份"段落式"段表（状态机会认它）。"""
    table: dict = {
        "idle1": {"range": [0, 183]},
        "idle_to_talk": {"range": [184, 274]},
        "talk": {"range": [275, 442]},
        "talk_to_idle": {"range": [443, 533]},
        "_total": 534,
    }
    for index, action in enumerate(actions):
        lo = 600 + index * 100
        table[action] = {"range": [lo, lo + 60]}
    (avatar_dir / "ranges.json").write_text(
        json.dumps(table, ensure_ascii=False), encoding="utf-8"
    )


class AvatarActionTests(AssetsTestBase):
    """形象有哪些动作，要看**段表**，不能拿帧数猜。

    实测教训：yy 只有 1077 帧但段表里配了 comfort，早先用
    ``frames >= 1751`` 判断"有没有编排"，把它误报成"无动作编排"。
    """

    def test_actions_from_segment_table_not_frame_count(self):
        make_avatar(self.avatars, "full", frames=2000)
        make_avatar(self.avatars, "short", frames=1077)
        write_segment_table(self.avatars / "short", actions=("comfort",))
        by_id = {item["id"]: item for item in assets.list_avatars()}
        # 没段表 -> 默认时间线，四个动作齐全
        self.assertEqual(
            ["greet", "encourage", "praise", "comfort"], by_id["full"]["actions"]
        )
        self.assertTrue(by_id["full"]["choreography"])
        # 有段表 -> 以段表为准；帧数少不代表没有编排
        self.assertEqual(["comfort"], by_id["short"]["actions"])
        self.assertTrue(by_id["short"]["choreography"])

    def test_partial_segment_table_reports_only_listed_actions(self):
        make_avatar(self.avatars, "part", frames=1000)
        write_segment_table(self.avatars / "part", actions=("greet", "comfort"))
        self.assertEqual(["greet", "comfort"], assets.avatar_actions("part"))

    def test_unusable_segment_table_falls_back_to_default(self):
        # 缺 idle/talk 的段表会被状态机忽略 -> 退回默认时间线（四个动作）
        make_avatar(self.avatars, "broken", frames=100)
        (self.avatars / "broken" / "ranges.json").write_text(
            json.dumps({"_total": 100, "comfort": {"range": [0, 50]}}),
            encoding="utf-8",
        )
        self.assertEqual(list(assets.ALL_ACTIONS), assets.avatar_actions("broken"))

    def test_unknown_avatar_has_no_actions(self):
        self.assertEqual([], assets.avatar_actions(""))

    def test_dance_flag_follows_segment_table(self):
        """dance 是独立能力标志（gen_dance.py 构建的舞蹈段），不进 LLM 动作清单。"""
        make_avatar(self.avatars, "dancer", frames=1408)
        table = {
            "idle1": {"range": [0, 183]},
            "idle_to_comfort": {"range": [184, 274]},
            "comfort": {"range": [275, 442]},
            "comfort_to_talk": {"range": [443, 533]},
            "talk": {"range": [534, 801]},
            "talk_to_idle": {"range": [802, 892]},
            "dance": {"range": [893, 1041]},
            "dance_to_idle": {"range": [802, 892]},
            "_total": 1042,
        }
        (self.avatars / "dancer" / "ranges.json").write_text(
            json.dumps(table, ensure_ascii=False), encoding="utf-8"
        )
        by_id = {item["id"]: item for item in assets.list_avatars()}
        self.assertTrue(by_id["dancer"]["dance"])
        # dance 不该混进 LLM 语义动作清单
        self.assertNotIn("dance", by_id["dancer"]["actions"])
        # 没有段表（默认时间线）的形象没有舞蹈段
        make_avatar(self.avatars, "plain", frames=2000)
        by_id = {item["id"]: item for item in assets.list_avatars()}
        self.assertFalse(by_id["plain"]["dance"])
        self.assertFalse(assets.avatar_has_dance(""))
        # 目录不存在 -> 段表读不到，按默认时间线算
        self.assertEqual(list(assets.ALL_ACTIONS), assets.avatar_actions("nobody"))


class VoiceTests(AssetsTestBase):
    def test_voice_listing_and_activation(self):
        (self.drop / "teacher.wav").write_bytes(b"w")
        (self.drop / "teacher.txt").write_text("你好呀，我是老师。", encoding="utf-8")
        (self.drop / "broken.wav").write_bytes(b"w")  # 缺 txt

        voices = assets.list_voices()
        by_id = {v["id"]: v for v in voices}
        self.assertIn("default", by_id)
        self.assertTrue(by_id["teacher"]["hasText"])
        self.assertFalse(by_id["broken"]["hasText"])
        # 没有缓存文件时遗留音色不出现
        self.assertNotIn("zzf2", by_id)

        # 缺文本的音色不能激活
        ok, message = assets.set_active_voice("broken")
        self.assertFalse(ok)
        self.assertIn("txt", message)

        ok, _ = assets.set_active_voice("teacher")
        self.assertTrue(ok)
        self.assertEqual("teacher", assets.active_voice_id())
        info = assets.active_voice_info()
        self.assertIsNotNone(info)
        self.assertEqual("ref", info["type"])
        self.assertEqual(self.drop / "teacher.wav", info["path"])
        self.assertEqual("你好呀，我是老师。", info["text"])
        self.assertEqual("voice_teacher", info["spk_id"])

        # 切回默认
        ok, _ = assets.set_active_voice("default")
        self.assertTrue(ok)
        self.assertIsNone(assets.active_voice_info())

    def test_legacy_voice_zzf2(self):
        cache = self.voices / "zzf2_spkinfo.pt"
        # 缓存缺失：列表不出现、不能激活
        ok, message = assets.set_active_voice("zzf2")
        self.assertFalse(ok)
        self.assertNotIn("zzf2", [v["id"] for v in assets.list_voices()])

        # 缓存就位：可列出、可激活，返回 legacy 描述
        cache.write_bytes(b"fake")
        voices = {v["id"]: v for v in assets.list_voices()}
        self.assertIn("zzf2", voices)
        self.assertEqual("legacy", voices["zzf2"]["type"])

        ok, _ = assets.set_active_voice("zzf2")
        self.assertTrue(ok)
        info = assets.active_voice_info()
        self.assertIsNotNone(info)
        self.assertEqual("legacy", info["type"])
        self.assertEqual(cache, info["path"])
        self.assertEqual("voice_zzf2", info["target_spk"])

        assets.set_active_voice("default")
        self.assertIsNone(assets.active_voice_info())

    def test_builtin_voice_prefers_reference_audio(self):
        """有参考音频时不能再用旧 spk2info.pt。

        旧缓存的 speech token 来自另一版 tokenizer（码本映射不同），而且
        注册音频含 2.1s 前导静音，会把合成语速一起带偏到 0.36 s/字。
        """
        cache = self.voices / "zzf2_spkinfo.pt"
        cache.write_bytes(b"fake")
        ok, _ = assets.set_active_voice("zzf2")
        self.assertTrue(ok)
        # 只有旧缓存：退回 legacy 描述
        info = assets.active_voice_info()
        self.assertIsNotNone(info)
        self.assertEqual("legacy", info["type"])
        self.assertEqual(cache, info["path"])

        # 参考音频就位：改走 ref 路径（按当前模型重新注册）
        ref = self.voices / "zzf2_ref.wav"
        ref.write_bytes(b"w")
        info = assets.active_voice_info()
        self.assertEqual("ref", info["type"])
        self.assertEqual(ref, info["path"])
        self.assertEqual("voice_zzf2", info["spk_id"])
        self.assertTrue(info["text"])

    def test_builtin_voice_listed_from_reference_audio_alone(self):
        # 旧缓存被清掉、只剩参考音频时，音色依然要能列出并激活
        (self.voices / "zzf2_ref.wav").write_bytes(b"w")
        self.assertIn("zzf2", [v["id"] for v in assets.list_voices()])
        ok, _ = assets.set_active_voice("zzf2")
        self.assertTrue(ok)
        self.assertEqual("ref", assets.active_voice_info()["type"])

    def test_activation_survives_and_falls_back(self):
        (self.drop / "a.wav").write_bytes(b"w")
        (self.drop / "a.txt").write_text("文本", encoding="utf-8")
        assets.set_active_voice("a")
        self.assertEqual("a", assets.active_voice_id())
        # 文件被删后回退默认
        (self.drop / "a.wav").unlink()
        self.assertIsNone(assets.active_voice_info())

    def test_marker_and_gen_logs_are_not_voices(self):
        (self.voices / "live_spk.json").write_text("{}", encoding="utf-8")
        (self.voices / "_active.json").write_text("{}", encoding="utf-8")
        self.assertEqual(["default"], [v["id"] for v in assets.list_voices()])


class DropVideoTests(AssetsTestBase):
    def test_drop_videos_listing(self):
        (self.drop / "xiaoming.mp4").write_bytes(b"v")
        (self.drop / "中文.mp4").write_bytes(b"v")
        (self.drop / "zzf2.mp4").write_bytes(b"v")
        (self.drop / "note.txt").write_bytes(b"t")
        videos = assets.list_drop_videos()
        self.assertEqual(["xiaoming"], [v["name"] for v in videos])
        self.assertFalse(videos[0]["built"])

        make_avatar(self.avatars, "xiaoming", frames=10)
        videos = assets.list_drop_videos()
        self.assertTrue(videos[0]["built"])

    def test_generation_validation(self):
        ok, message = assets.start_avatar_generation("nobody")
        self.assertFalse(ok)
        self.assertIn("没有找到", message)

        ok, message = assets.start_avatar_generation("bad name")
        self.assertFalse(ok)

        # 与遗留音色同名的视频不参与人物生成，避免名字冲突
        (self.drop / "zzf2.mp4").write_bytes(b"v")
        ok, message = assets.start_avatar_generation("zzf2")
        self.assertFalse(ok)

        (self.drop / "done.mp4").write_bytes(b"v")
        make_avatar(self.avatars, "done", frames=10)
        ok, message = assets.start_avatar_generation("done")
        self.assertFalse(ok)
        self.assertIn("已存在", message)


class SnapshotTests(AssetsTestBase):
    def test_snapshot_shape(self):
        make_avatar(self.avatars, "my_avatar")
        make_avatar(self.avatars, "yy", frames=1077)
        (self.drop / "v.wav").write_bytes(b"w")
        (self.drop / "v.txt").write_text("t", encoding="utf-8")
        snap = assets.snapshot()
        self.assertEqual(["my_avatar", "yy"], [a["id"] for a in snap["avatars"]])
        self.assertEqual("my_avatar", snap["defaultAvatar"])
        self.assertIn("default", [v["id"] for v in snap["voices"]])
        # 页面上的"数字人"列表：一项 = 一个人物 + 一个音色
        self.assertEqual(["zzf", "yy"], [p["id"] for p in snap["presets"]][:2])
        self.assertIn("drop", snap["dropDir"].replace("/", "\\").lower())


class PresetTests(AssetsTestBase):
    """页面上的"数字人"列表：一项 = 一个人物 + 一个音色的固定搭配。

    表里写死的配对（zzf / yy）排在最前；没配对的素材自动兜底，避免新投递
    的东西从页面上消失。核心不变式：**每一项都必须同时有人物和音色**。
    """

    def presets(self) -> dict:
        return {p["id"]: p for p in assets.list_presets()}

    def ready_yy(self) -> None:
        make_avatar(self.avatars, "my_avatar")
        make_avatar(self.avatars, "yy", frames=1077)
        (self.voices / "zzf2_ref.wav").write_bytes(b"w")

    def test_zzf_and_yy_pairs_are_declared(self):
        self.ready_yy()
        presets = assets.list_presets()
        # 配对表里的排在最前，顺序固定
        self.assertEqual(["zzf", "yy"], [p["id"] for p in presets])
        zzf, yy = presets
        # zzf = 初始形象 + 人物训练音色
        self.assertEqual("my_avatar", zzf["avatar"])
        self.assertEqual("default", zzf["voice"])
        self.assertTrue(zzf["ready"])
        # yy = yy 形象 + zzf2 配对音色
        self.assertEqual("yy", yy["avatar"])
        self.assertEqual("zzf2", yy["voice"])
        self.assertTrue(yy["ready"])

    def test_preset_note_reports_actions(self):
        # 页面上要能一眼看出这个形象会不会做动作、会做哪几个
        make_avatar(self.avatars, "my_avatar")
        make_avatar(self.avatars, "yy", frames=1077)
        write_segment_table(self.avatars / "yy", actions=("comfort",))
        (self.voices / "zzf2_ref.wav").write_bytes(b"w")
        presets = self.presets()
        self.assertIn("动作齐全", presets["zzf"]["note"])
        self.assertEqual(
            ["greet", "encourage", "praise", "comfort"], presets["zzf"]["actions"]
        )
        self.assertIn("动作：comfort", presets["yy"]["note"])
        self.assertEqual(["comfort"], presets["yy"]["actions"])

    def test_avatar_missing_marks_preset_unready(self):
        # 只生成了 my_avatar，yy 形象还没有
        make_avatar(self.avatars, "my_avatar")
        (self.voices / "zzf2_ref.wav").write_bytes(b"w")
        yy = self.presets()["yy"]
        self.assertFalse(yy["ready"])
        self.assertIn("yy", yy["reason"])

    def test_voice_missing_marks_preset_unready(self):
        make_avatar(self.avatars, "my_avatar")
        make_avatar(self.avatars, "yy", frames=1077)
        # zzf2 的旧缓存和参考音频都没有
        yy = self.presets()["yy"]
        self.assertFalse(yy["ready"])
        self.assertIn("zzf2", yy["reason"])

    def test_unpaired_avatar_falls_back_to_default_voice(self):
        make_avatar(self.avatars, "my_avatar")
        make_avatar(self.avatars, "xiaoming")
        xiaoming = self.presets()["xiaoming"]
        self.assertEqual("xiaoming", xiaoming["avatar"])
        self.assertEqual("default", xiaoming["voice"])
        self.assertTrue(xiaoming["ready"])
        self.assertIn("未配对", xiaoming["note"])

    def test_unpaired_voice_attaches_to_default_avatar(self):
        make_avatar(self.avatars, "my_avatar")
        (self.drop / "teacher.wav").write_bytes(b"w")
        (self.drop / "teacher.txt").write_text("文本", encoding="utf-8")
        teacher = self.presets()["teacher"]
        self.assertEqual("my_avatar", teacher["avatar"])
        self.assertEqual("teacher", teacher["voice"])
        self.assertTrue(teacher["ready"])

    def test_unpaired_voice_without_text_is_unready(self):
        make_avatar(self.avatars, "my_avatar")
        (self.drop / "notext.wav").write_bytes(b"w")
        self.assertFalse(self.presets()["notext"]["ready"])

    def test_every_preset_carries_both_avatar_and_voice(self):
        # "以后这种都搞成配对的"：列表里每一项都必须同时有人物和音色
        self.ready_yy()
        make_avatar(self.avatars, "xiaoming")
        (self.drop / "a.wav").write_bytes(b"w")
        (self.drop / "a.txt").write_text("t", encoding="utf-8")
        presets = assets.list_presets()
        self.assertEqual(4, len(presets))
        for preset in presets:
            self.assertTrue(preset["avatar"], preset)
            self.assertTrue(preset["voice"], preset)
            self.assertIn("ready", preset)
            self.assertIn("note", preset)


class ReadmeSyncTests(AssetsTestBase):
    """投递说明要跟着模板走，但不能覆盖用户自己改过的内容。"""

    def readme(self) -> Path:
        return self.drop / "README-先读我.txt"

    def test_readme_written_on_init(self):
        self.assertTrue(self.readme().is_file())
        self.assertIn("数字人", self.readme().read_text(encoding="utf-8"))

    def test_template_change_refreshes_untouched_readme(self):
        # 内容还是上次由我们写出去的 -> 模板变了就该跟着变
        with mock.patch.object(assets, "_README_TEXT", "新模板内容"):
            assets.init(SimpleNamespace(avatar_id="my_avatar"))
        self.assertEqual("新模板内容", self.readme().read_text(encoding="utf-8"))

    def test_user_edited_readme_is_preserved(self):
        self.readme().write_text("我自己写的备注", encoding="utf-8")
        with mock.patch.object(assets, "_README_TEXT", "新模板内容"):
            assets.init(SimpleNamespace(avatar_id="my_avatar"))
        self.assertEqual("我自己写的备注", self.readme().read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
