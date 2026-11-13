# -*- coding: utf-8 -*-
"""语音克隆投递：用户读一句话 → 质检 → 落引擎投递箱 → 绑定到照片数字人。

机制（复用引擎既有约定，零引擎改动）：
  引擎把 ``data/drop/<音色id>.wav`` + ``<音色id>.txt``（转录文本）当作一个
  可切换的 zero-shot 克隆音色（server/assets.py 的 list_voices），
  ``data/presets.json`` 里把该数字人的 ``voice`` 指成这个 id 即自动配对。
  所以本模块只做三件事：录音质检、素材落箱、注册表绑定。

质检标准（对齐 CosyVoice3 zero-shot 实测）：
  - 容器 wav；16kHz 单声道、16bit（不符则重采样/转单声道，不做上采样欺骗）
  - 时长 3~15 秒（太短音色学不稳，太长首句合成等待久）
  - RMS 能量 0.02~0.5（太轻底噪成音色、太响削波失真）
  - 最长静音 ≤ 1.5s（zzf2 旧素材前 2.1s 静音把语速带偏的教训）
"""
import json
import re
import shutil
import uuid
import wave
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from core.logger import get_logger
from core.responses import success

logger = get_logger(__name__)

router = APIRouter(tags=["数字人制作"])

ENGINE_DIR = Path(__file__).resolve().parents[2] / "digital-human" / "livetalking"
DROP_DIR = ENGINE_DIR / "data" / "drop"
PRESETS_FILE = ENGINE_DIR / "data" / "presets.json"
SENTENCES_DIR = ENGINE_DIR / "data" / "voice_sentences"

VOICE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,23}$")

# 录制引导句：短、覆盖常用音节、口语自然（长句用户读着累，容易破音）
SENTENCES = [
    "今天我们在这里聊聊天，说说最近发生在身边的那些有趣的事情。",
    "如果感到累或者不开心，可以随时找我，我会一直在这里陪着你。",
    "早上起床记得喝一杯温水，呼吸一下新鲜空气，让自己精神起来。",
    "无论天气怎样，都希望你今天能拥有一点点属于自己的小快乐。",
]

MIN_SECONDS, MAX_SECONDS = 3.0, 15.0
MIN_RMS, MAX_RMS = 0.02, 0.5
MAX_SILENCE = 1.5


def _safe_voice_id(name: str) -> bool:
    return bool(VOICE_ID_RE.match(name or ""))


def _load_wav(path: Path):
    """读 wav → (float32 单声道 [-1,1], sr, n_channels, sampwidth)。"""
    with wave.open(str(path), "rb") as w:
        n, sw, sr = w.getnframes(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
        ch = w.getnchannels()
    if sw == 2:
        pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 1:
        pcm = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 4:
        pcm = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"不支持的采样位深 {sw * 8}bit")
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1)
    return pcm, sr, ch, sw


def _write_wav(path: Path, pcm: np.ndarray, sr: int) -> None:
    pcm16 = np.clip(pcm, -1.0, 1.0)
    pcm16 = (pcm16 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.tobytes())


def _resample(pcm: np.ndarray, sr: int, target: int = 16000) -> np.ndarray:
    if sr == target:
        return pcm
    n = int(round(len(pcm) * target / sr))
    return np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm)


def _longest_silence(pcm: np.ndarray, sr: int, frame_ms: int = 20, th: float = 0.01) -> float:
    step = max(1, int(sr * frame_ms / 1000))
    best = cur = 0
    for i in range(0, len(pcm) - step, step):
        seg = pcm[i:i + step]
        if float(np.sqrt(np.mean(seg * seg))) < th:
            cur += step
            best = max(best, cur)
        else:
            cur = 0
    return best / sr


def _inspect(path: Path) -> dict:
    """质检 → 基本信息或抛 HTTPException(422, 人话原因)。"""
    try:
        pcm, sr, ch, sw = _load_wav(path)
    except Exception as exc:
        raise HTTPException(422, f"音频格式无法解析（需要 wav）：{exc}")
    if len(pcm) < sr * MIN_SECONDS:
        raise HTTPException(422, f"录音太短（{len(pcm) / sr:.1f}秒）——请把整句话读完，至少 3 秒")
    if len(pcm) > sr * MAX_SECONDS:
        raise HTTPException(422, f"录音太长（{len(pcm) / sr:.1f}秒）——只录引导句那一句，15 秒以内")
    rms = float(np.sqrt(np.mean(pcm * pcm)))
    if rms < MIN_RMS:
        raise HTTPException(422, "录音声音太小——凑近麦克风，用正常说话音量再录一次")
    if rms > MAX_RMS:
        raise HTTPException(422, "录音声音过大（可能破音）——离麦克风远一点再录一次")
    silence = _longest_silence(pcm, sr)
    if silence > MAX_SILENCE:
        raise HTTPException(
            422, f"录音里有 {silence:.1f} 秒以上的空白——中间不要停顿太久，一句话读完")
    return {"duration": round(len(pcm) / sr, 2), "rms": round(rms, 3),
            "sample_rate": sr, "channels": ch, "silence": round(silence, 2)}


@router.get("/dh/voice/sentences")
async def voice_sentences():
    """录制引导句清单（每次随机一条，前端可换句）。"""
    return success({"sentences": SENTENCES})


# 官方备用音色（CosyVoice instruct2 原生生成的常驻男女声，落投递箱即普通音色；
# 没录自己声音的用户二选一，选中即随照片数字人上架绑定）
OFFICIAL_VOICES = [
    {"id": "official_male", "label": "男声 · 备用", "desc": "真人男声样本（清晰自然）"},
    {"id": "official_female", "label": "女声 · 备用", "desc": "真人女主持样本（清晰自然）"},
]


@router.get("/dh/voice/official")
async def official_voices():
    """官方备用音色清单（素材缺失时 available=false）。"""
    result = []
    for spec in OFFICIAL_VOICES:
        vid = spec["id"]
        ok = (DROP_DIR / f"{vid}.wav").is_file() and (DROP_DIR / f"{vid}.txt").is_file()
        result.append({**spec, "available": ok})
    return success({"voices": result})


class VoiceBind(BaseModel):
    voice_id: str
    avatar_id: str


@router.post("/dh/voice/upload")
async def voice_upload(audio: UploadFile = File(...)):
    """上传一句录音 → 质检 → 转 16k 单声道 → 落引擎投递箱 + 引导句文本。

    音色 id 形如 ``vc_<job8>``（job8=uuid 前 8 位），全局唯一不撞旧素材。
    返回 voice_id 与质检信息（时长/能量），供前端展示。
    """
    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "音频内容为空")
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(400, "音频不能超过 8MB")
    voice_id = f"vc_{uuid.uuid4().hex[:8]}"
    if not _safe_voice_id(voice_id):
        raise HTTPException(500, "音色 id 生成异常，请重试")

    DROP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DROP_DIR / f"_tmp_{voice_id}.wav"
    tmp.write_bytes(raw)
    try:
        info = _inspect(tmp)
    except HTTPException:
        tmp.unlink(missing_ok=True)
        raise
    # 16k 单声道 16bit 重写（CosyVoice3 注册要求；不做假上采样——
    # sr 已经是 16k 的录音在浏览器 MediaRecorder 下最常见）
    pcm, sr, ch, sw = _load_wav(tmp)
    pcm = _resample(pcm, sr, 16000)
    final = DROP_DIR / f"{voice_id}.wav"
    _write_wav(final, pcm, 16000)
    tmp.unlink(missing_ok=True)
    # 引导句文本：zero-shot 的 prompt 文本必须与音频内容一致，否则音色/语速
    # 都会走样（引擎读同名 .txt）。前端录的是哪句就存哪句，默认第一条。
    text_file = DROP_DIR / f"{voice_id}.txt"
    if not text_file.exists():
        text_file.write_text(SENTENCES[0], encoding="utf-8")
    logger.info("语音克隆素材已投递 voice_id=%s %s", voice_id, info)
    return success({"voice_id": voice_id, "info": info, "text": text_file.read_text(encoding="utf-8")})


@router.post("/dh/voice/bind")
async def voice_bind(body: VoiceBind):
    """把克隆音色绑定到照片数字人（写 presets.json 注册表）。

    引擎 list_presets 读 presets.json：voice 指到投递箱里的音色 id，
    页面上这个数字人一选中就同时换上形象+音色，不再需要手动切音色。
    """
    if not _safe_voice_id(body.voice_id) or not _safe_voice_id(body.avatar_id):
        raise HTTPException(400, "音色 id 或数字人 id 不合法")
    wav = DROP_DIR / f"{body.voice_id}.wav"
    txt = DROP_DIR / f"{body.voice_id}.txt"
    if not (wav.is_file() and txt.is_file()):
        raise HTTPException(404, "音色素材不存在，请先上传录音")
    avatar_dir = ENGINE_DIR / "data" / "avatars" / body.avatar_id
    if not avatar_dir.is_dir():
        raise HTTPException(404, f"数字人「{body.avatar_id}」不存在")
    if body.avatar_id == body.voice_id:
        raise HTTPException(400, "音色 id 不能与数字人 id 相同")

    presets = []
    if PRESETS_FILE.is_file():
        try:
            presets = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("presets.json 解析失败，按空表重建")
            presets = []
    if not isinstance(presets, list):
        presets = []
    found = None
    for item in presets:
        if isinstance(item, dict) and item.get("id") == body.avatar_id:
            found = item
            break
    if found is None:
        found = {"id": body.avatar_id, "name": body.avatar_id,
                 "avatar": body.avatar_id, "note": "照片生成 · 克隆音色"}
        presets.append(found)
    found["voice"] = body.voice_id
    found.setdefault("avatar", body.avatar_id)
    found.setdefault("name", body.avatar_id)
    found["note"] = "照片生成 · 语音克隆音色"
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_FILE.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("克隆音色已绑定 avatar=%s voice=%s", body.avatar_id, body.voice_id)
    return success({"avatar_id": body.avatar_id, "voice_id": body.voice_id})


@router.get("/dh/voice/list")
async def voice_list():
    """投递箱里的克隆音色（前端克隆记录列表）。"""
    if not DROP_DIR.is_dir():
        return {"voices": []}
    voices = []
    for wav in sorted(DROP_DIR.glob("vc_*.wav")):
        voice_id = wav.stem
        txt = DROP_DIR / f"{voice_id}.txt"
        bound_to = ""
        if PRESETS_FILE.is_file():
            try:
                for item in json.loads(PRESETS_FILE.read_text(encoding="utf-8")):
                    if isinstance(item, dict) and item.get("voice") == voice_id:
                        bound_to = str(item.get("id") or "")
                        break
            except Exception:
                pass
        voices.append({"id": voice_id, "hasText": txt.is_file(),
                       "boundTo": bound_to,
                       "sizeKb": round(wav.stat().st_size / 1024)})
    return success({"voices": voices})
