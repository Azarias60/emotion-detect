"""Persona blocks: one markdown file binds a prompt and an optional voice.

Borrowed from VoxEMW's persona building block. Each ``personas/*.md`` carries
frontmatter with a display name, an optional description and an optional
zero-shot voice reference; the body is the LLM system prompt.

A persona may also declare ``mode:`` — ``default``（轻量页）、``live``（实时口型）
或 ``any``（两者通用）。两个前端是两个不同的角色、配两套不同的音色，因此每个
模式各自维护"当前人设"，不再共用一个全局选择。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

from utils.logger import logger

ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "personas"
DEFAULT_PROMPT = "你是一个友好的数字人助手。"
MODES = ("default", "live")
MODE_ANY = "any"


@dataclass
class Persona:
    id: str
    name: str = ""
    description: str = ""
    prompt: str = DEFAULT_PROMPT
    ref_wav: str = ""
    ref_text: str = ""
    # Natural-language voice direction for CosyVoice instruct2 synthesis, e.g.
    # "用低龄小男孩的声音说话". Takes effect without any reference audio.
    instruct: str = ""
    # Deterministic post-ASR corrections, "错写=正写" pairs separated by
    # commas: Whisper reliably mishears uncommon persona names.
    hotwords: str = ""
    # 该人设服务的模式：default / live / any
    mode: str = MODE_ANY

    @property
    def has_voice(self) -> bool:
        return bool(self.ref_wav and self.ref_text)

    @property
    def has_instruct_voice(self) -> bool:
        return bool(self.instruct)

    def serves(self, mode: str | None) -> bool:
        """该人设是否可用于指定模式（mode 为空时表示通用查询）。"""
        if not mode:
            return True
        return self.mode in (mode, MODE_ANY)

    def correction_map(self) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for item in self.hotwords.split(","):
            item = item.strip()
            if "=" in item:
                wrong, _, right = item.partition("=")
                wrong, right = wrong.strip(), right.strip()
                if wrong and right:
                    pairs[wrong] = right
        return pairs

    def correct(self, text: str) -> str:
        pairs = self.correction_map()
        if not pairs or not text:
            return text
        # Single pass with longest-first alternation so an earlier
        # replacement can never re-trigger a shorter wrong key.
        pattern = re.compile(
            "|".join(re.escape(key) for key in sorted(pairs, key=len, reverse=True))
        )
        return pattern.sub(lambda match: pairs[match.group(0)], text)


def _safe_id(stem: str) -> str | None:
    return stem if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", stem) else None


def _safe_mode(value: str) -> str:
    mode = (value or "").strip().lower()
    return mode if mode in MODES else MODE_ANY


def parse_persona(path: Path) -> Persona | None:
    """Parse one persona markdown file; invalid ids or unreadable files are skipped."""
    persona_id = _safe_id(path.stem)
    if persona_id is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    meta: dict[str, str] = {}
    lines = text.strip().splitlines()
    body = text.strip()
    if lines and lines[0].strip() == "---":
        end = None
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                end = index
                break
        if end is not None:
            for line in lines[1:end]:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip().strip("'\"")
            body = "\n".join(lines[end + 1 :]).strip()

    return Persona(
        id=persona_id,
        name=meta.get("name") or persona_id,
        description=meta.get("description", ""),
        prompt=body or DEFAULT_PROMPT,
        ref_wav=meta.get("ref_wav", ""),
        ref_text=meta.get("ref_text", ""),
        instruct=meta.get("instruct", ""),
        hotwords=meta.get("hotwords", ""),
        mode=_safe_mode(meta.get("mode", "")),
    )


_lock = threading.RLock()
_cache: dict[str, Persona] | None = None
# 全局兜底选择（mode=any 的人设，或旧调用方不带模式时使用）
_active_id = ""
# 模式 -> 该模式当前人设 id
_active_mode_ids: dict[str, str] = {}


def load_personas(directory: str | Path | None = None) -> dict[str, Persona]:
    """(Re)load the persona directory and return the parsed registry."""
    global _cache
    base = Path(directory) if directory else PERSONA_DIR
    personas: dict[str, Persona] = {}
    if base.is_dir():
        for path in sorted(base.glob("*.md")):
            persona = parse_persona(path)
            if persona is not None:
                personas[persona.id] = persona
    with _lock:
        _cache = personas
        for mode, persona_id in list(_active_mode_ids.items()):
            if persona_id not in personas:
                _active_mode_ids.pop(mode, None)
    return personas


def _registry() -> dict[str, Persona]:
    return _cache if _cache is not None else load_personas()


def _pick_for_mode(personas: dict[str, Persona], mode: str | None) -> Persona:
    """该模式下应生效的人设：显式选择 > 声明该模式的人设 > 通用人设 > 第一个。"""
    if mode:
        chosen = personas.get(_active_mode_ids.get(mode, ""))
        if chosen is not None and chosen.serves(mode):
            return chosen
        for persona in personas.values():
            if persona.mode == mode:
                return persona
        global_choice = personas.get(_active_id)
        if global_choice is not None and global_choice.serves(mode):
            return global_choice
    global_choice = personas.get(_active_id)
    if global_choice is not None:
        return global_choice
    return next(iter(personas.values()))


def get_persona(persona_id: str) -> Persona | None:
    with _lock:
        return _registry().get(persona_id)


def get_active_persona(mode: str | None = None) -> Persona | None:
    """当前生效的人设；带 mode 时按模式解析（default / live）。"""
    with _lock:
        personas = _registry()
        if not personas:
            return None
        return _pick_for_mode(personas, mode)


def set_active_persona(persona_id: str) -> Persona | None:
    """切换人设：带 mode 声明的人设只影响自己那个模式。"""
    global _active_id
    with _lock:
        persona = _registry().get(persona_id)
        if persona is None:
            logger.warning("persona not found: %s", persona_id)
            return None
        if persona.mode in MODES:
            _active_mode_ids[persona.mode] = persona_id
        else:
            _active_id = persona_id
    logger.info(
        "active persona -> %s (%s, mode=%s)", persona.id, persona.name, persona.mode
    )
    return persona


def active_system_prompt(mode: str | None = None, fallback: str | None = None) -> str:
    persona = get_active_persona(mode)
    if persona is not None and persona.prompt:
        return persona.prompt
    return fallback or DEFAULT_PROMPT


def summary(mode: str | None = None) -> dict:
    """Payload for the /api/personas endpoints（按模式返回当前人设与可选列表）。"""
    with _lock:
        personas = _registry()
        if not personas:
            personas = {"default": Persona(id="default", name="默认助手")}
        active = _pick_for_mode(personas, mode)
        items = [
            {
                "id": persona.id,
                "name": persona.name,
                "description": persona.description,
                "voice": persona.has_voice,
                "mode": persona.mode,
                "active": persona.id == active.id,
            }
            for persona in personas.values()
            if persona.serves(mode)
        ]
        if not items:
            items = [
                {
                    "id": active.id,
                    "name": active.name,
                    "description": active.description,
                    "voice": active.has_voice,
                    "mode": active.mode,
                    "active": True,
                }
            ]
        return {
            "active": active.id,
            "name": active.name,
            "mode": active.mode,
            "personas": items,
        }
