from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from server import personas


class PersonaTests(unittest.TestCase):
    def _write(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_parse_persona_with_frontmatter(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "hero.md",
                "---\n"
                "name: 小勇\n"
                "description: 勇敢的伙伴\n"
                "ref_wav: personas/hero/voice.wav\n"
                "ref_text: 这是参考文本\n"
                "instruct: 用低龄小男孩的声音说话\n"
                "---\n"
                "你是小勇，说话干脆利落。\n",
            )
            persona = personas.parse_persona(root / "hero.md")
            self.assertIsNotNone(persona)
            self.assertEqual("hero", persona.id)
            self.assertEqual("小勇", persona.name)
            self.assertEqual("勇敢的伙伴", persona.description)
            self.assertEqual("你是小勇，说话干脆利落。", persona.prompt)
            self.assertTrue(persona.has_voice)
            self.assertEqual("personas/hero/voice.wav", persona.ref_wav)
            self.assertTrue(persona.has_instruct_voice)
            self.assertEqual("用低龄小男孩的声音说话", persona.instruct)

    def test_parse_persona_without_frontmatter_keeps_default_prompt(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "plain.md", "自定义提示词正文")
            persona = personas.parse_persona(root / "plain.md")
            self.assertIsNotNone(persona)
            self.assertEqual("plain", persona.id)
            self.assertEqual("自定义提示词正文", persona.prompt)
            self.assertFalse(persona.has_voice)

    def test_invalid_id_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "bad name.md", "正文")
            self.assertIsNone(personas.parse_persona(root / "bad name.md"))

    def test_load_and_switch_active(self):
        with TemporaryDirectory() as tmp:
            registry = personas.load_personas(tmp)
            self.assertEqual({}, registry)
            self._write(Path(tmp), "a.md", "---\nname: 甲\n---\n甲的提示词")
            self._write(Path(tmp), "b.md", "---\nname: 乙\n---\n乙的提示词")
            registry = personas.load_personas(tmp)
            self.assertEqual({"a", "b"}, set(registry))
            # Active defaults to the first persona and switching updates it.
            self.assertEqual("a", personas.get_active_persona().id)
            switched = personas.set_active_persona("b")
            self.assertEqual("b", switched.id)
            self.assertEqual("乙的提示词", personas.active_system_prompt())
            self.assertIsNone(personas.set_active_persona("missing"))
            payload = personas.summary()
            self.assertEqual("b", payload["active"])
            self.assertEqual({"a", "b"}, {item["id"] for item in payload["personas"]})


class PersonaModeTests(unittest.TestCase):
    """两个前端是两套人设：轻量页 default，实时口型 live。"""

    def _write(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.write_text(content, encoding="utf-8")
        return path

    def _two_mode_personas(self, tmp: str) -> None:
        self._write(
            Path(tmp),
            "nuan.md",
            "---\nname: 暖暖\nmode: default\nhotwords: 换换=暖暖\n---\n暖暖的提示词",
        )
        self._write(
            Path(tmp),
            "care.md",
            "---\nname: 数字关怀助手\nmode: live\n---\n关怀伙伴的提示词",
        )
        personas.load_personas(tmp)

    def test_mode_is_parsed_and_validated(self):
        with TemporaryDirectory() as tmp:
            self._write(Path(tmp), "a.md", "---\nname: 甲\nmode: live\n---\n甲")
            self._write(Path(tmp), "b.md", "---\nname: 乙\nmode: nonsense\n---\n乙")
            self._write(Path(tmp), "c.md", "---\nname: 丙\n---\n丙")
            personas.load_personas(tmp)
            self.assertEqual("live", personas.get_persona("a").mode)
            self.assertEqual("any", personas.get_persona("b").mode)
            self.assertEqual("any", personas.get_persona("c").mode)

    def test_each_mode_resolves_its_own_persona(self):
        with TemporaryDirectory() as tmp:
            self._two_mode_personas(tmp)
            self.assertEqual("nuan", personas.get_active_persona("default").id)
            self.assertEqual("care", personas.get_active_persona("live").id)
            self.assertEqual(
                "暖暖的提示词", personas.active_system_prompt(mode="default")
            )
            self.assertEqual(
                "关怀伙伴的提示词", personas.active_system_prompt(mode="live")
            )

    def test_summary_lists_only_the_modes_personas(self):
        with TemporaryDirectory() as tmp:
            self._two_mode_personas(tmp)
            self.assertEqual(
                {"nuan"}, {p["id"] for p in personas.summary("default")["personas"]}
            )
            self.assertEqual(
                {"care"}, {p["id"] for p in personas.summary("live")["personas"]}
            )
            self.assertEqual("default", personas.summary("default")["mode"])
            self.assertEqual("live", personas.summary("live")["mode"])

    def test_switching_one_mode_leaves_the_other_alone(self):
        with TemporaryDirectory() as tmp:
            self._two_mode_personas(tmp)
            self.assertIsNotNone(personas.set_active_persona("care"))
            self.assertEqual("care", personas.get_active_persona("live").id)
            self.assertEqual("nuan", personas.get_active_persona("default").id)

    def test_hotwords_are_scoped_to_their_own_mode(self):
        with TemporaryDirectory() as tmp:
            self._two_mode_personas(tmp)
            self.assertEqual(
                "暖暖你好", personas.get_active_persona("default").correct("换换你好")
            )
            # 实时口型的人设没有热词，不会被轻量页的纠错污染
            self.assertEqual(
                "换换你好", personas.get_active_persona("live").correct("换换你好")
            )

    def test_shipped_personas_cover_both_modes(self):
        personas.load_personas()
        self.assertEqual("nuan", personas.get_active_persona("default").id)
        self.assertEqual("care", personas.get_active_persona("live").id)
        self.assertIn("暖暖", personas.active_system_prompt(mode="default"))
        self.assertNotIn("暖暖", personas.active_system_prompt(mode="live"))


if __name__ == "__main__":
    unittest.main()
