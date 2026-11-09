"""人物与音色素材库：统一管理可切换的 avatar 与 drop-in 音色。

设计要点：
- 素材只来自两个地方：``data/avatars/<id>/``（就绪的人物）和 ``data/drop/``
  （用户随手放进来的人物视频 / 音色 wav+txt）。
- 旧流程遗留的素材（zzf1 / zzf2 / yy 等）在 ``IGNORED_IDS`` 黑名单里，
  枚举、切换、构建会话时一概不可见、不可用——这就是"停止检测"的实现。
- 音色切换是进程内热切换：写 ``data/voices/_active.json``，TTS 每次合成前
  读取（tts/cosyvoice3.py 的 ``_live_speaker_id``），下一句立即生效。
- 人物视频生成走子进程跑 ``avatars/wav2lip/genavatar_stream.py``，
  状态与日志尾部缓存在内存里供前端轮询。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from utils.logger import logger

# 段表解析复用状态机那一套（单一事实来源，避免两处各写一份段名规则）
from state_machine import TYPE_NAMES, load_avatar_plan

# dance 是特殊动作段（gen_dance.py 构建进段表），状态机里状态号为 10
DANCE_STATE = 10

ROOT = Path(__file__).resolve().parents[1]
AVATARS_DIR = ROOT / "data" / "avatars"
DROP_DIR = ROOT / "data" / "drop"
VOICES_DIR = ROOT / "data" / "voices"
GEN_LOG_DIR = DROP_DIR / "_gen"

# 旧流程遗留、仍被忽略的素材 id（zzf2 素材已改名为 yy 并进入可选列表）
IGNORED_IDS = {"zzf1"}

# 人物 ↔ 音色配对预设：页面上一项就是一个"数字人"。
#
# 为什么改成配对：人物和音色分开选，会选出"yy 的形象 + my_avatar 的声音"
# 这类不搭的组合 —— 形象和声音对不上，而且每个音色的注册语速、每套形象的
# 口型素材本来就是配套调好的（zzf2 的参考音频就是 yy 素材那套录音）。
# 现在一项 = 一对，点了两个一起换。
#
# 名字沿用素材名，不另起新名：
#   zzf = 初始形象 my_avatar + 人物训练音色（default）
#   yy  = yy 形象 + zzf2 配对音色
#
# 新投递的素材没写进这张表时，list_presets() 会自动兜底配对
# （人物配人物训练音色、音色挂初始形象），保证不会从页面上消失。
PRESETS = [
    {
        "id": "zzf",
        "name": "zzf",
        "avatar": "my_avatar",
        "voice": "default",
        "note": "初始形象 + 人物训练音色",
    },
    {
        "id": "yy",
        "name": "yy",
        "avatar": "yy",
        "voice": "zzf2",
        "note": "yy 形象 + zzf2 配对音色",
    },
]

# 遗留训练音色缓存：来自旧流程的 spk2info.pt（导入时自动补 <|endofprompt|>）
# 查找顺序：data/voices/<id>_spkinfo.pt → data/avatars/<avatar>/spk2info.pt
#
# 注意 ref_wav / ref_text：有参考音频时优先用它按**当前模型**重新注册
# （tts/cosyvoice3.py 的 _ensure_ref_speaker），旧的 spk2info.pt 只作后备。
# 原因有两个，都是实测出来的：
#   1. 旧缓存的 speech token 来自另一版 speech tokenizer，码本映射不同，
#      直接搬运会让音色走样；
#   2. zzf2 那份缓存的 prompt 音频 5.01s 里前 2.1s 是静音，14 个字只占
#      2.9s —— zero-shot 会把"14 字 = 5 秒"当语速学走，合成被拉慢到
#      0.36 s/字（正常 0.21），听感就是拖沓、段落末尾多念。
#      data/voices/zzf2_ref.wav 是同一段录音裁掉首尾静音后的结果
#      （2.97s / 14 字 = 0.212 s/字）。
LEGACY_VOICES = {
    "zzf2": {
        "name": "zzf2（yy 配对音色）",
        "avatar": "yy",
        # 实测缓存内的音色 key（导入函数还有单 key 兜底）
        "source_spk": "zzf2_spk",
        "target_spk": "voice_zzf2",
        "ref_wav": "zzf2_ref.wav",
        "ref_text": "这是我们发明的智能情绪探测器",
    },
}

SAFE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv"}
IMAGE_RE = re.compile(r"\.[jpJP][pnPN]*[gG]$")
ACTIVE_VOICE_FILE = VOICES_DIR / "_active.json"

_lock = threading.RLock()
_opt = None
_jobs: dict[str, dict] = {}


def _safe_id(value: str) -> bool:
    return bool(SAFE_ID_RE.fullmatch(value or ""))


def init(opt) -> None:
    """启动时调用：建目录、写说明、记住默认配置。"""
    global _opt
    _opt = opt
    for directory in (DROP_DIR, VOICES_DIR, GEN_LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    _sync_readme(DROP_DIR)
    logger.info(
        "[Assets] 素材库就绪: 人物=%s 音色/视频投递=%s",
        AVATARS_DIR, DROP_DIR,
    )


def _sync_readme(directory: Path) -> None:
    """把投递说明刷成当前模板，但不动用户手工改过的内容。

    判断"用户改过没有"：上一次由我们写出去的内容摘要存在
    ``_readme.sha256``。文件内容仍等于那个摘要 -> 没人动过，模板变了就
    覆盖；对不上 -> 用户自己编辑过，原样保留。没有摘要文件（首次升级到
    这一版）时以模板为准 —— 说明文件是生成物，旧版内容已经和界面不符。
    """
    readme = directory / "README-先读我.txt"
    marker = directory / "_readme.sha256"
    digest = hashlib.sha256(_README_TEXT.encode("utf-8")).hexdigest()

    def digest_of(path: Path) -> str | None:
        """按**文本**算摘要。

        ``write_text`` 在 Windows 上会把 ``\\n`` 写成 ``\\r\\n``，按字节
        比对会和 ``_README_TEXT.encode()`` 永远不相等；``read_text`` 会把
        换行归一成 ``\\n``，用它算才自洽。
        """
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    try:
        last_written = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        last_written = ""

    if readme.is_file():
        current = digest_of(readme)
        if current == digest:
            marker.write_text(digest, encoding="utf-8")
            return
        if last_written and current is not None and current != last_written:
            logger.info("[Assets] 投递说明被手工改过，保留原内容: %s", readme)
            return
    try:
        readme.write_text(_README_TEXT, encoding="utf-8")
        marker.write_text(digest, encoding="utf-8")
    except OSError:
        logger.exception("投递说明写入失败: %s", readme)


_README_TEXT = """\
【LiveTalking 素材投递文件夹使用说明】

面板上的每一项都是一个"数字人"= 一套固定的「形象 + 声音」搭配，
点一下两个一起换。自带两组：zzf（初始形象 + 人物训练音色）、
yy（yy 形象 + zzf2 配对音色）。

一、放人物视频（成为可切换的形象）
  1. 把视频改成一个简单的英文名，例如 xiaoming.mp4（不要用中文名）。
  2. 把它放到本文件夹（data/drop/）。
  3. 打开网页的"实时口型"页面，在"数字人"面板里点【生成】。
     服务器会在后台做人脸检测和裁剪（视频越长越久，几分钟到几十分钟）。
  4. 生成完成后，面板里会多出一项 xiaoming，点它即可切换。
     新形象默认配"人物训练音色"，想换声音再单独调。
  注意：随手拍的视频没有"待机/打招呼/安慰"等动作段落，切换后数字人
  只会循环播放视频并做口型，不启用动作编排（需要 1751 帧以上的
  专门素材才带动作）。想重建人物：删除 data/avatars/<名字> 后重新生成。

二、放音色（成为可切换的说话声音）
  1. 录一段 5~10 秒清晰的话，存成 wav，例如 teacher.wav。
  2. 再建一个同名文本文件 teacher.txt，内容必须是这段话的逐字文字
     （一字不差，保存为 UTF-8 编码）。文本与音频不匹配音色会失真。
  3. 两个文件都放进本文件夹。
  4. 网页"实时口型"页面的"数字人"面板里点【刷新】，会多出一项 teacher
     （挂初始形象），点了下一句话立即用新声音。
  提示：录音请把首尾的静音剪掉 —— 静音会被当成"语速"一起学走，合成
  就变慢。系统注册前会自动裁一次，但源头干净更稳。

三、其他
  - 文件夹里以下划线开头的子文件夹（如 _gen）是系统日志，请勿修改。
  - 更早遗留的 zzf1 素材仍被系统忽略。
"""


# ── 人物 ────────────────────────────────────────────────────────────────

def _avatar_frames(avatar_dir: Path) -> int:
    img_dir = avatar_dir / "full_imgs"
    if not img_dir.is_dir():
        return 0
    return sum(1 for item in img_dir.iterdir() if IMAGE_RE.search(item.name))


def _avatar_ready(avatar_dir: Path) -> bool:
    return (
        (avatar_dir / "coords.pkl").is_file()
        and (avatar_dir / "face_imgs").is_dir()
        and _avatar_frames(avatar_dir) > 0
    )


def is_allowed_avatar(avatar_id: str) -> bool:
    """合法且不在黑名单内的人物 id（供 /offer 构建会话时校验）。"""
    return _safe_id(avatar_id or "") and avatar_id not in IGNORED_IDS


# 语义动作标签（与 state_machine.ACTION_MAP 一致）
ALL_ACTIONS = ("greet", "encourage", "praise", "comfort")


def avatar_actions(avatar_id: str) -> list[str]:
    """该形象**真正能播**的动作名。

    有自定义段表（``<avatar>/ranges.json``）就以段表为准；没有段表、或段表
    不完整到会被状态机忽略，则按默认时间线算 —— 四个动作齐全。

    **不能拿帧数猜**：yy 只有 1077 帧，但段表里确实配了 comfort；
    早先用 ``frames >= 1751`` 判断"有没有编排"，会把 yy 误报成"无动作编排"。
    """
    if not avatar_id:
        return []
    plan = load_avatar_plan(AVATARS_DIR / avatar_id / "ranges.json")
    if plan is None:
        return list(ALL_ACTIONS)
    names = {TYPE_NAMES.get(state) for state in plan["action_ranges"]}
    return [name for name in ALL_ACTIONS if name in names]


def _action_note(actions: list[str]) -> str:
    """给页面看的动作摘要。"""
    if not actions:
        return "无动作段"
    if len(actions) == len(ALL_ACTIONS):
        return "动作齐全"
    return "动作：" + "/".join(actions)


def avatar_has_dance(avatar_id: str) -> bool:
    """该形象的段表里有没有 dance 段（页面据此显示/隐藏"跳舞"按钮）。"""
    if not avatar_id:
        return False
    plan = load_avatar_plan(AVATARS_DIR / avatar_id / "ranges.json")
    return plan is not None and DANCE_STATE in plan["action_ranges"]


def list_avatars() -> list[dict]:
    """列出全部可用人物（就绪的才进列表，黑名单永不出现）。"""
    result: list[dict] = []
    if not AVATARS_DIR.is_dir():
        return result
    for entry in sorted(AVATARS_DIR.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or not _safe_id(entry.name):
            continue
        if entry.name in IGNORED_IDS:
            continue
        if not _avatar_ready(entry):
            continue
        actions = avatar_actions(entry.name)
        result.append({
            "id": entry.name,
            "frames": _avatar_frames(entry),
            "actions": actions,
            # 有动作段才算有编排 —— 按段表判断，不按帧数
            "choreography": bool(actions),
            # 舞蹈段是独立能力：没编入 actions（那是 LLM 语义动作的清单）
            "dance": avatar_has_dance(entry.name),
        })
    return result


# ── 投递箱：人物视频与音色 ───────────────────────────────────────────────

def list_drop_videos() -> list[dict]:
    """data/drop 里尚未生成人物的视频。"""
    result: list[dict] = []
    if not DROP_DIR.is_dir():
        return result
    for entry in sorted(DROP_DIR.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        stem = entry.stem
        if not _safe_id(stem) or stem in IGNORED_IDS or stem in LEGACY_VOICES:
            continue
        built = _avatar_ready(AVATARS_DIR / stem)
        result.append({
            "name": stem,
            "file": entry.name,
            "built": built,
            "generating": _jobs.get(stem, {}).get("status") == "running",
        })
    return result


def legacy_voice_cache(voice_id: str) -> Path | None:
    """遗留音色的缓存文件路径（不存在返回 None）。"""
    spec = LEGACY_VOICES.get(voice_id)
    if spec is None:
        return None
    candidates = [
        VOICES_DIR / f"{voice_id}_spkinfo.pt",
        AVATARS_DIR / spec.get("avatar", "") / "spk2info.pt",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def voice_ref_audio(voice_id: str) -> Path | None:
    """内置音色的参考音频路径（不存在返回 None）。

    相对路径按 ``VOICES_DIR`` 解析（与 legacy 缓存同一目录）；也接受绝对
    路径，方便把参考音频放在别处。

    有参考音频就不该用旧 spk2info.pt：旧缓存的 speech token 来自另一版
    tokenizer，而且注册时的音频可能带长段静音（zzf2 前 2.1s），会把合成
    语速一起带偏。
    """
    spec = LEGACY_VOICES.get(voice_id)
    if spec is None:
        return None
    raw = spec.get("ref_wav")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = VOICES_DIR / path
    return path if path.is_file() else None


def list_voices() -> list[dict]:
    """可切换音色：默认训练音色 + 遗留训练音色 + data/drop 里的 wav+txt 对。"""
    result = [{
        "id": "default",
        "name": "默认（人物训练音色）",
        "hasText": True,
    }]
    for voice_id, spec in LEGACY_VOICES.items():
        if legacy_voice_cache(voice_id) is not None or voice_ref_audio(voice_id) is not None:
            result.append({
                "id": voice_id,
                "name": spec["name"],
                "type": "legacy",
                "hasText": True,
            })
    if not DROP_DIR.is_dir():
        return result
    for entry in sorted(DROP_DIR.iterdir(), key=lambda p: p.name):
        if entry.suffix.lower() != ".wav":
            continue
        stem = entry.stem
        if not _safe_id(stem) or stem in IGNORED_IDS or stem in LEGACY_VOICES:
            continue
        result.append({
            "id": stem,
            "name": stem,
            "type": "ref",
            "hasText": (DROP_DIR / f"{stem}.txt").is_file(),
        })
    return result


# ── 人物↔音色配对预设（页面上的"数字人"列表） ─────────────────────────

def _avatar_ready_by_id(avatar_id: str) -> bool:
    return bool(avatar_id) and _avatar_ready(AVATARS_DIR / avatar_id)


def _voice_available(voice_id: str) -> bool:
    """音色素材是否可用（``default`` 永远可用，它走人物训练音色链路）。"""
    if voice_id == "default":
        return True
    if voice_id in LEGACY_VOICES:
        return (
            legacy_voice_cache(voice_id) is not None
            or voice_ref_audio(voice_id) is not None
        )
    return (
        (DROP_DIR / f"{voice_id}.wav").is_file()
        and (DROP_DIR / f"{voice_id}.txt").is_file()
    )


def _default_avatar_id() -> str:
    return getattr(_opt, "avatar_id", "my_avatar") if _opt else "my_avatar"


def _preset_entry(
    preset_id: str, name: str, avatar: str, voice: str, note: str
) -> dict:
    if not _avatar_ready_by_id(avatar):
        reason = f"人物素材 {avatar} 未就绪"
    elif not _voice_available(voice):
        reason = f"音色 {voice} 的素材缺失"
    else:
        reason = ""
    actions = avatar_actions(avatar) if not reason else []
    return {
        "id": preset_id,
        "name": name,
        "avatar": avatar,
        "voice": voice,
        # 页面上直接把动作摘要写在说明里，省得再猜"这个形象会不会做动作"
        "note": f"{note} · {_action_note(actions)}" if note else _action_note(actions),
        "actions": actions,
        "ready": not reason,
        "reason": reason,
    }


def list_presets() -> list[dict]:
    """页面上的"数字人"列表：一项 = 一个人物 + 一个音色的固定搭配。

    ``PRESETS`` 里写死的配对排在最前；剩下没配对的素材自动兜底 ——
    人物配人物训练音色、音色挂初始形象 —— 这样新投递的素材不会因为
    "没写进配对表"就从页面上消失。每项带 ``ready`` / ``reason``，
    前端据此置灰并给出原因。
    """
    result: list[dict] = []
    seen_avatars: set[str] = set()
    seen_voices: set[str] = set()

    for spec in PRESETS:
        result.append(
            _preset_entry(
                spec["id"],
                spec.get("name") or spec["id"],
                spec["avatar"],
                spec["voice"],
                spec.get("note", ""),
            )
        )
        seen_avatars.add(spec["avatar"])
        seen_voices.add(spec["voice"])

    # 外部配对文件 data/presets.json：studio 生产线（照片→数字人）生成的新数字人
    # 用声明式注册，不必改代码。字段与 PRESETS 相同：id/name/avatar/voice/note。
    # 顺序排在写死配对之后、自动兜底之前；id 冲突的条目跳过。
    extra_file = ROOT / "data" / "presets.json"
    if extra_file.is_file():
        try:
            extra = json.loads(extra_file.read_text(encoding="utf-8"))
            if isinstance(extra, list):
                known_ids = {item["id"] for item in result}
                for spec in extra:
                    if not isinstance(spec, dict):
                        continue
                    avatar = str(spec.get("avatar") or "")
                    if not avatar or not _safe_id(avatar):
                        logger.warning("[Assets] presets.json 条目缺少合法 avatar，已跳过: %r", spec)
                        continue
                    pid = str(spec.get("id") or avatar)
                    if not _safe_id(pid) or pid in known_ids:
                        continue
                    result.append(
                        _preset_entry(
                            pid,
                            str(spec.get("name") or pid),
                            avatar,
                            str(spec.get("voice") or "default"),
                            str(spec.get("note") or ""),
                        )
                    )
                    known_ids.add(pid)
                    seen_avatars.add(avatar)
                    seen_voices.add(str(spec.get("voice") or "default"))
        except Exception:
            logger.warning("[Assets] %s 解析失败，已忽略", extra_file)

    for avatar in list_avatars():
        avatar_id = avatar["id"]
        if avatar_id in seen_avatars:
            continue
        seen_avatars.add(avatar_id)
        result.append(
            _preset_entry(
                avatar_id,
                avatar_id,
                avatar_id,
                "default",
                "未配对：自动使用人物训练音色",
            )
        )

    default_avatar = _default_avatar_id()
    for voice in list_voices():
        voice_id = voice["id"]
        if voice_id == "default" or voice_id in seen_voices:
            continue
        seen_voices.add(voice_id)
        result.append(
            _preset_entry(
                voice_id,
                voice_id,
                default_avatar,
                voice_id,
                "未配对：自动使用初始形象",
            )
        )
    return result


# ── 音色激活状态（热切换） ──────────────────────────────────────────────

def active_voice_id() -> str:
    try:
        data = json.loads(ACTIVE_VOICE_FILE.read_text(encoding="utf-8"))
        return str(data.get("id") or "default")
    except (OSError, json.JSONDecodeError, ValueError):
        return "default"


def set_active_voice(voice_id: str) -> tuple[bool, str]:
    """切换全局激活音色；返回 (成功, 消息)。"""
    with _lock:
        if voice_id == "default":
            _write_active_voice("default")
            logger.info("[Assets] 音色切回默认训练音色")
            return True, "已切回默认音色"
        if voice_id in LEGACY_VOICES:
            if voice_ref_audio(voice_id) is not None:
                _write_active_voice(voice_id)
                logger.info("[Assets] 激活内置音色: %s（参考音频注册）", voice_id)
                return True, f"已切换音色 {voice_id}（yy 配对音色）"
            if legacy_voice_cache(voice_id) is None:
                return False, f"遗留音色 {voice_id} 的缓存文件缺失"
            _write_active_voice(voice_id)
            logger.info("[Assets] 激活遗留训练音色: %s", voice_id)
            return True, f"已切换音色 {voice_id}（yy 配对训练音色）"
        wav = DROP_DIR / f"{voice_id}.wav"
        txt = DROP_DIR / f"{voice_id}.txt"
        if not _safe_id(voice_id) or not wav.is_file():
            return False, f"音色 {voice_id} 不存在（需要 data/drop/{voice_id}.wav）"
        if not txt.is_file():
            return False, f"缺少逐字文本 data/drop/{voice_id}.txt，音色无法注册"
        _write_active_voice(voice_id)
        logger.info("[Assets] 激活投递音色: %s", voice_id)
        return True, f"已切换音色 {voice_id}"


def _write_active_voice(voice_id: str) -> None:
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_VOICE_FILE.write_text(
        json.dumps({"id": voice_id}, ensure_ascii=False), encoding="utf-8"
    )


def active_voice_info() -> dict | None:
    """供 TTS 每次合成前查询当前激活音色，返回 None 表示默认训练音色链路。

    遗留训练音色返回 ``{"type": "legacy", "path", "source_spk", "target_spk"}``；
    投递参考音色返回 ``{"type": "ref", "path", "text", "spk_id"}``。
    文件缺失时同样返回 None（回退默认，说话永不中断）。
    """
    voice_id = active_voice_id()
    if voice_id == "default":
        return None
    if voice_id in LEGACY_VOICES:
        spec = LEGACY_VOICES[voice_id]
        ref = voice_ref_audio(voice_id)
        if ref is not None:
            # 有参考音频：走 ref 路径按当前模型重新注册（音色准、语速正常）
            return {
                "type": "ref",
                "path": ref,
                "text": spec.get("ref_text", ""),
                "spk_id": spec["target_spk"],
            }
        cache = legacy_voice_cache(voice_id)
        if cache is None:
            logger.warning("[Assets] 遗留音色 %s 缓存缺失，回退默认音色", voice_id)
            return None
        return {
            "type": "legacy",
            "path": cache,
            "source_spk": spec["source_spk"],
            "target_spk": spec["target_spk"],
        }
    wav = DROP_DIR / f"{voice_id}.wav"
    txt = DROP_DIR / f"{voice_id}.txt"
    if not wav.is_file() or not txt.is_file():
        logger.warning("[Assets] 激活音色 %s 的文件缺失，回退默认音色", voice_id)
        return None
    try:
        text = txt.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        logger.warning("[Assets] %s 不是 UTF-8 文本，回退默认音色", txt)
        return None
    if not text:
        return None
    return {"type": "ref", "path": wav, "text": text, "spk_id": f"voice_{voice_id}"}


# ── 人物视频生成（子进程跑 genavatar_stream） ──────────────────────────

def start_avatar_generation(name: str) -> tuple[bool, str]:
    """把 data/drop/<name>.mp4 后台生成为 data/avatars/<name>/。"""
    if not _safe_id(name):
        return False, "人物名只能用英文、数字、下划线和短横线"
    video = DROP_DIR / f"{name}.mp4"
    if not video.is_file():
        return False, f"没有找到 data/drop/{name}.mp4"
    if name in IGNORED_IDS:
        return False, f"{name} 是系统忽略的遗留素材名"
    if name in LEGACY_VOICES:
        return False, f"{name} 与内置音色同名，请给人物换一个名字"
    target = AVATARS_DIR / name
    if _avatar_ready(target):
        return False, f"人物 {name} 已存在；如需重建请先删除 data/avatars/{name}"
    with _lock:
        job = _jobs.get(name)
        if job and job.get("status") == "running":
            return False, f"{name} 正在生成中"
        GEN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = GEN_LOG_DIR / f"{name}.log"
        log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
        try:
            proc = subprocess.Popen(
                [
                    sys.executable, "-u",
                    str(ROOT / "avatars" / "wav2lip" / "genavatar_stream.py"),
                    "--avatar_id", name,
                    "--video_path", str(video),
                ],
                cwd=str(ROOT),
                stdout=log_fh,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            log_fh.close()
            return False, f"启动生成进程失败: {exc}"
        _jobs[name] = {
            "status": "running",
            "pid": proc.pid,
            "log": str(log_path),
            "started": time.time(),
            "_proc": proc,
            "_fh": log_fh,
        }
    logger.info("[Assets] 开始生成人物 %s（pid %s）", name, proc.pid)
    return True, f"开始生成人物 {name}，完成后可在人物列表切换"


def _refresh_jobs() -> None:
    """收割已结束的生成进程，更新状态与日志尾部。"""
    with _lock:
        for name, job in _jobs.items():
            if job.get("status") != "running":
                continue
            proc = job.get("_proc")
            if proc is None:
                continue
            code = proc.poll()
            if code is None:
                continue
            fh = job.pop("_fh", None)
            if fh is not None:
                fh.close()
            if code == 0 and _avatar_ready(AVATARS_DIR / name):
                job["status"] = "done"
                job["message"] = "生成完成，可以切换了"
            else:
                job["status"] = "error"
                job["message"] = f"生成失败（退出码 {code}），详见日志"
            job["finished"] = time.time()
            logger.info("[Assets] 人物 %s 生成结束: %s", name, job["status"])


def _log_tail(path: Path, lines: int = 4) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])
    except OSError:
        return ""


def snapshot() -> dict:
    """前端 /api/assets 的载荷。"""
    _refresh_jobs()
    with _lock:
        generations = [
            {
                "name": name,
                "status": job["status"],
                "message": job.get("message", ""),
                "tail": _log_tail(Path(job["log"])) if job["status"] == "running" else "",
            }
            for name, job in sorted(_jobs.items())
            if job["status"] != "done" or time.time() - job.get("finished", 0) < 60
        ]
    default_avatar = _default_avatar_id()
    return {
        "avatars": list_avatars(),
        "defaultAvatar": default_avatar,
        "voices": list_voices(),
        "activeVoice": active_voice_id(),
        # 页面上的"数字人"列表：一项 = 一个人物 + 一个音色的固定搭配
        "presets": list_presets(),
        "dropVideos": list_drop_videos(),
        "generations": generations,
        "dropDir": str(DROP_DIR),
    }
