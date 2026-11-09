"""In-process Fun-CosyVoice3 streaming TTS with a persisted zero-shot voice."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from threading import RLock
from typing import Iterator

import numpy as np
import torch

from registry import register
from utils.logger import logger
from .base_tts import BaseTTS, State

ROOT = Path(__file__).resolve().parents[1]
COSYVOICE_REPO = ROOT / "cosyvoice_repo"
MATCHA_REPO = COSYVOICE_REPO / "third_party" / "Matcha-TTS"

FIXED_SPEAKER_ID = "livetalking_fixed_voice"
FIXED_REFERENCE_AUDIO = ROOT / "mp4" / "sound_ref.wav"
FIXED_REFERENCE_TEXT = "光之国的家是他们的故乡，初代佐菲杰克来自这地方"
COSYVOICE3_PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"
# cosyvoice_repo/cosyvoice/llm/llm.py:479 硬断言要求 prompt_text 里含该 token。
# 通过 add_zero_shot_spk 注册的音色都带前缀；而 zzf1/zzf2 流程写出的
# data/avatars/*/spk2info.pt 缓存没有前缀，直接使用会让上游 llm_job 断言
# 崩溃（见 HANDOFF_FIXES.md P0-A），因此导入时必须补回来。
COSYVOICE3_ENDOFPROMPT_TOKEN = 151646

# ── 暖暖专属音色：云霞卡通（guide_boy_v1）──
# 2026-10-24 用户拍板：暖暖全线（导览页 + 轻量默认页）只用云霞卡通音色；
# 原默认页男孩音色（livetalking_default_boy_v5，2.6 秒短样本、音色发闷，
# 用户实测否决）已删除，不再注册、不再可选。v5 的注册结果残留在
# spk2info.pt 缓存里，但没有任何代码路径会再选中它。
# 云霞卡通样本：edge-tts 云霞（zh-CN-YunxiaNeural，Male+Cartoon+Cute）
# + 语速 +8%/音调 +6Hz 生成的 11.8 秒导览语气长样本，四候选试听后选定。
GUIDE_BOY_SPK_ID = "guide_boy_v1"
GUIDE_BOY_REF_WAV = ROOT / "mp4" / "guide_boy_ref.wav"
# 注册配对转写（与 guide_boy_ref.wav 逐字一致），不会作为台词念出
GUIDE_BOY_REF_TEXT = (
    "大家好呀，我是健康监测中心的小导览员。今天由我来陪你一起看看身体的各项小指标，"
    "别紧张，检测一点都不疼，很快就完成啦。"
)

# 实时口型模式（WebRTC 数字人）的专属音色：从参考音频用**当前模型**注册，
# 保证 speech token 与当前 speech_tokenizer 同一码本（旧流程缓存来自另一版
# 分词器，码本大小相同但码字映射不同，直接搬运会导致音色不对）。
# 注册结果按参考音频的 sha256 追踪，音频/文本一变就自动重注册。
# 默认参考音频是项目自带的“初代佐菲杰克”男声（mp4/sound_ref.wav）。
LIVE_SPK_ID_DEFAULT = "live_spk"
LIVE_REF_FILE_DEFAULT = ROOT / "mp4" / "sound_ref.wav"
LIVE_REF_TEXT_DEFAULT = "光之国的家是他们的故乡，初代佐菲杰克来自这地方"
LIVE_SPK_MARKER_DIR = ROOT / "data" / "voices"
# 裁剪首尾静音后的参考音频缓存（按内容 sha256 命名，一次生成长期复用）
REF_TRIM_DIR = ROOT / "data" / "voices" / "_ref"


def _rms_frames(sample: np.ndarray, sample_rate: int, window_s: float = 0.02):
    """按 window_s 切帧算 RMS，返回 (每帧 rms, 每帧样本数)。"""
    frame = max(1, int(sample_rate * window_s))
    count = sample.size // frame
    if count <= 0:
        return np.empty(0, dtype=np.float32), frame
    blocks = sample[: count * frame].reshape(count, frame)
    return np.sqrt(np.mean(blocks * blocks, axis=1)), frame


def _trim_edges(
    sample: np.ndarray,
    sample_rate: int,
    rel_threshold: float = 0.05,
    pad_s: float = 0.06,
) -> np.ndarray:
    """裁掉首尾静音，两端各留 ``pad_s`` 秒（幂等）。

    阈值取"帧能量峰值 × rel_threshold"而不是绝对值：不同录音的电平差
    一两个数量级，绝对阈值在低电平录音上会整段判成静音。
    """
    rms, frame = _rms_frames(sample, sample_rate)
    if rms.size == 0:
        return sample
    peak = float(rms.max())
    if peak <= 1e-4:
        return sample
    voiced = np.nonzero(rms >= max(peak * rel_threshold, 1e-3))[0]
    if voiced.size == 0:
        return sample
    start = max(0, int(voiced[0] * frame - pad_s * sample_rate))
    end = min(sample.size, int((voiced[-1] + 1) * frame + pad_s * sample_rate))
    if end - start < sample_rate * 0.5:
        return sample  # 裁出不足 0.5 秒，判为误裁，原样保留
    return sample[start:end]


def _prepared_ref_audio(audio_path: Path) -> Path:
    """注册用的参考音频：裁掉首尾静音后缓存，返回可直接注册的路径。

    录音里常见 1~2 秒前导静音（按下录制键之后才开口）。CosyVoice 的
    zero-shot 会把"整段时长 ÷ 文本字数"当语速一起学走 —— zzf2 的样本
    5.01s 里前 2.1s 是静音，14 个字只占 2.9s，于是合成语速被拉慢到
    0.36 s/字（正常 0.21），听感就是拖沓、段落末尾多念。裁掉首尾静音后
    回到 0.212 s/字，prompt token 也从 125 降到 74，首包一并变快。

    读不了 / 裁不动时原样返回原路径：注册永不因裁剪失败而中断。
    """
    try:
        import soundfile as sf

        sample, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
    except Exception:
        return audio_path
    if sample.size == 0:
        return audio_path
    mono = np.ascontiguousarray(sample[:, 0])
    trimmed = _trim_edges(mono, int(sample_rate))
    if trimmed.size == mono.size:
        return audio_path
    digest = hashlib.sha256(trimmed.tobytes()).hexdigest()[:16]
    target = REF_TRIM_DIR / f"{audio_path.stem}_{digest}.wav"
    if not target.is_file():
        try:
            REF_TRIM_DIR.mkdir(parents=True, exist_ok=True)
            sf.write(str(target), trimmed, int(sample_rate))
        except Exception:
            logger.exception("参考音频裁剪结果写入失败: %s", target)
            return audio_path
    logger.info(
        "参考音频裁掉首尾静音: %.2fs -> %.2fs（%s）",
        mono.size / sample_rate,
        trimmed.size / sample_rate,
        audio_path.name,
    )
    return target


def _trim_after_first_pause(
    sample: np.ndarray, sample_rate: int, pause_s: float = 0.6, floor: float = 0.01
) -> np.ndarray:
    """Cut the registration sample at its first long pause.

    instruct2 keeps hallucinating speech after the sample sentence ends
    (sentence → ~1s silence → extra babble). Registering that tail would
    pair the prompt text with audio it does not describe, degrading the
    zero-shot speaker. Trim at the first pause of `pause_s` or longer.
    """
    frame = int(sample_rate * 0.05)
    quiet_run = 0
    last_loud_end = 0
    for offset in range(0, sample.size, frame):
        seg = sample[offset : offset + frame]
        rms = float(np.sqrt(np.mean(seg * seg))) if seg.size else 0.0
        if rms < floor:
            quiet_run += seg.size
            if quiet_run >= int(sample_rate * pause_s):
                break
        else:
            quiet_run = 0
            last_loud_end = offset + seg.size
    return sample[:last_loud_end]

_model = None
_model_dir = None
_model_lock = RLock()
_modelscope_cache_patched = False

# 预热进度（/api/readiness 供前端加载页轮询）：
#   pending → 尚未开始；loading → 模型加载/试推中；
#   ready  → 预热完成，第一句话不再付加载延迟；failed → 预热出错
_warm_state = "pending"
_warm_error = ""


def warm_status() -> dict:
    return {"state": _warm_state, "error": _warm_error}


def _add_repo_paths() -> None:
    for path in (COSYVOICE_REPO, MATCHA_REPO):
        if not path.is_dir():
            raise FileNotFoundError(
                f"CosyVoice3 代码目录不存在: {path}。"
                "请从 https://github.com/FunAudioLLM/CosyVoice 下载。"
            )
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def _prefer_modelscope_cache() -> None:
    """Keep WeText startup offline once its small FST bundle is cached."""
    global _modelscope_cache_patched
    if _modelscope_cache_patched:
        return

    import modelscope

    download = modelscope.snapshot_download

    def local_first(model_id, *args, **kwargs):
        if "local_files_only" in kwargs:
            return download(model_id, *args, **kwargs)
        try:
            return download(model_id, *args, local_files_only=True, **kwargs)
        except Exception:
            return download(model_id, *args, **kwargs)

    modelscope.snapshot_download = local_first
    _modelscope_cache_patched = True


def _resolve_model_dir(path: str | Path) -> Path:
    model_path = Path(path).expanduser()
    return model_path if model_path.is_absolute() else ROOT / model_path


def _load_model(model_dir: str | Path = "models/Fun-CosyVoice3-0.5B"):
    """Load one shared CosyVoice3 model in FP16 on CUDA."""
    global _model, _model_dir, _warm_state, _warm_error
    resolved = _resolve_model_dir(model_dir)
    with _model_lock:
        if _model is not None:
            if resolved != _model_dir:
                raise RuntimeError(
                    f"CosyVoice3 已从 {_model_dir} 加载，不能切换到 {resolved}"
                )
            return _model
        if not (resolved / "cosyvoice3.yaml").is_file():
            raise FileNotFoundError(f"CosyVoice3 模型不完整: {resolved}")

        _warm_state = "loading"
        _warm_error = ""
        _add_repo_paths()
        _prefer_modelscope_cache()
        from cosyvoice.cli.cosyvoice import AutoModel

        logger.info("加载 Fun-CosyVoice3: %s", resolved)
        started = time.perf_counter()
        try:
            _model = AutoModel(
                model_dir=str(resolved),
                fp16=torch.cuda.is_available(),
                load_trt=False,
                load_vllm=False,
            )
        except Exception as exc:
            _warm_state = "failed"
            _warm_error = str(exc)
            raise
        _model_dir = resolved
        try:
            _install_llm_job_guard(_model)
        except Exception:
            # 防护只是加固，装不上也不能影响模型加载。
            logger.exception("安装 llm_job 挂死防护失败（不影响本次加载）")
        logger.info("Fun-CosyVoice3 加载完成: %.2fs", time.perf_counter() - started)
        return _model


def _llm_job_owner(model):
    """找到真正持有 llm_job / llm_end_dict 的对象。

    ``AutoModel`` 返回的是 ``CosyVoice3`` 外壳（只有 list_available_spks 等
    门面方法），``llm_job`` 与 ``llm_end_dict`` 都在它的 ``.model`` 内层模型上。
    """
    for candidate in (model, getattr(model, "model", None)):
        if candidate is None:
            continue
        if hasattr(candidate, "llm_job") and hasattr(candidate, "llm_end_dict"):
            return candidate
    return None


def _install_llm_job_guard(model) -> bool:
    """让上游 llm_job 崩溃时流式循环仍能退出，而不是无声挂死。

    ``cosyvoice/cli/model.py::tts`` 的流式分支是::

        while True:
            ...yield...
            if self.llm_end_dict[uuid] is True and <token 不够>: break

    而 ``llm_end_dict[uuid]`` 只在 ``llm_job`` **正常返回**时置位。llm_job 里
    抛出的异常（例如 CosyVoice3 的 ``<|endofprompt|>`` 断言）会让线程直接死掉，
    标志位永远不为真 → 生成器永久空转 → 调用方既拿不到音频也拿不到异常，
    整条回复挂死（HANDOFF_FIXES.md P0-A）。这里在实例上给 ``llm_job`` 套一层
    ``finally``，保证异常路径也会置位，让上游循环正常退出并把异常抛给调用方。
    """
    owner = _llm_job_owner(model)
    if owner is None:
        logger.warning("未找到持有 llm_job 的对象，跳过挂死防护")
        return False
    if getattr(owner, "_livetalking_llm_job_guarded", False):
        return True

    original = owner.llm_job

    def guarded_llm_job(text, prompt_text, llm_prompt_speech_token, llm_embedding, uuid):
        try:
            return original(text, prompt_text, llm_prompt_speech_token, llm_embedding, uuid)
        finally:
            try:
                owner.llm_end_dict[uuid] = True
            except Exception:
                logger.exception("llm_end_dict 置位失败，上游可能出现挂死")

    owner.llm_job = guarded_llm_job
    owner._livetalking_llm_job_guarded = True
    logger.info("已为 %s 安装 llm_job 挂死防护", type(owner).__name__)
    return True


def _has_endofprompt(entry) -> bool:
    """该音色缓存是否已带 <|endofprompt|>（上游硬断言的要求）。"""
    if not isinstance(entry, dict):
        return False
    prompt_text = entry.get("prompt_text")
    prompt_text_len = entry.get("prompt_text_len")
    if prompt_text is None or prompt_text_len is None:
        return False
    length = int(prompt_text_len.reshape(-1)[0])
    return COSYVOICE3_ENDOFPROMPT_TOKEN in prompt_text.reshape(-1).tolist()[:length]


def _ensure_endofprompt_prefix(model, entry, spk_id: str) -> bool:
    """补回音色缓存缺失的 <|endofprompt|> 前缀，返回是否做了修改。

    prompt_text 用模型自己的分词器重建前缀，与 ``add_zero_shot_spk`` 注册的
    音色完全一致；语音 token / embedding 一概不动，因此音色本身不变。
    """
    if _has_endofprompt(entry):
        return False

    prompt_text = entry.get("prompt_text") if isinstance(entry, dict) else None
    prompt_text_len = entry.get("prompt_text_len") if isinstance(entry, dict) else None
    if prompt_text is None or prompt_text_len is None:
        logger.error("音色 %s 缓存缺少 prompt_text，无法补前缀", spk_id)
        return False

    length = int(prompt_text_len.reshape(-1)[0])
    frontend = getattr(model, "frontend", None)
    extract = getattr(frontend, "_extract_text_token", None)
    if extract is None:
        logger.error("无法为音色 %s 补前缀：模型没有 frontend._extract_text_token", spk_id)
        return False
    prefix_token, _prefix_len = extract(COSYVOICE3_PROMPT_PREFIX)
    prefix_token = prefix_token.to(device=prompt_text.device, dtype=prompt_text.dtype)
    if COSYVOICE3_ENDOFPROMPT_TOKEN not in prefix_token.reshape(-1).tolist():
        logger.error("分词器没有产出 <|endofprompt|>，拒绝使用音色 %s", spk_id)
        return False

    entry["prompt_text"] = torch.cat([prefix_token, prompt_text[:, :length]], dim=1)
    entry["prompt_text_len"] = torch.tensor(
        [entry["prompt_text"].shape[1]],
        dtype=prompt_text_len.dtype,
        device=prompt_text_len.device,
    )
    logger.info(
        "已为音色 %s 补回 <|endofprompt|> 前缀（%d -> %d tokens）",
        spk_id,
        length,
        entry["prompt_text"].shape[1],
    )
    return True


def _prepare_fixed_voice(
    model_dir: str | Path = "models/Fun-CosyVoice3-0.5B",
) -> str:
    """Create the project-wide fixed speaker cache once, then only reuse it."""
    model = _load_model(model_dir)
    with _model_lock:
        if FIXED_SPEAKER_ID in model.list_available_spks():
            logger.info("使用已缓存的 CosyVoice3 音色: %s", FIXED_SPEAKER_ID)
            return FIXED_SPEAKER_ID

        audio_path = FIXED_REFERENCE_AUDIO
        if not audio_path.is_file():
            raise FileNotFoundError(f"固定参考音频不存在: {audio_path}")

        prompt_text = f"{COSYVOICE3_PROMPT_PREFIX}{FIXED_REFERENCE_TEXT}"
        logger.info("首次提取固定 CosyVoice3 音色: %s", audio_path)
        model.add_zero_shot_spk(prompt_text, str(audio_path), FIXED_SPEAKER_ID)
        model.save_spkinfo()
        logger.info("固定音色已保存到: %s", _resolve_model_dir(model_dir) / "spk2info.pt")
        return FIXED_SPEAKER_ID


def _avatar_spk_cache_candidates(
    avatar_id: str = "", configured: str = ""
) -> list[Path]:
    """spk2info.pt 候选路径：实时口型音色随数字人素材一起存放。"""
    if configured:
        path = Path(configured).expanduser()
        return [path if path.is_absolute() else ROOT / path]
    if avatar_id:
        return [ROOT / "data" / "avatars" / avatar_id / "spk2info.pt"]
    return []


def _import_live_speaker(
    model,
    spk_id: str,
    *,
    avatar_id: str = "",
    cache_path: str = "",
) -> str | None:
    """把实时口型音色导入当前模型，成功返回音色 ID。

    zzf1 流程把训练音色注册到了数字人素材目录自己的 spk2info.pt，而
    CosyVoice3 只加载 <model_dir>/spk2info.pt，所以这里把该条目复制过来
    一次（不需要原始参考音频），随后所有请求都走已有的缓存音色路径。

    这些缓存缺少 ``<|endofprompt|>`` 前缀，导入前必须补齐，否则上游
    llm_job 断言崩溃并挂死整条回复；补不齐时宁可不使用该音色。
    """
    if not spk_id:
        return None
    with _model_lock:
        frontend = getattr(model, "frontend", None)
        if frontend is None or not hasattr(frontend, "spk2info"):
            logger.error(
                "CosyVoice3 模型缺少 frontend.spk2info，无法导入实时口型音色 %s", spk_id
            )
            return None

        if spk_id in model.list_available_spks():
            entry = frontend.spk2info.get(spk_id)
            if _ensure_endofprompt_prefix(model, entry, spk_id):
                _save_spkinfo(model, spk_id)
            if not _has_endofprompt(entry):
                logger.error(
                    "已缓存音色 %s 缺少 <|endofprompt|> 且无法补全，回退固定音色", spk_id
                )
                return None
            logger.info("实时口型音色已就绪: %s", spk_id)
            return spk_id

        for path in _avatar_spk_cache_candidates(avatar_id, cache_path):
            if not path.is_file():
                logger.warning("实时口型音色缓存不存在: %s", path)
                continue
            try:
                cached = torch.load(path, map_location="cpu", weights_only=True)
            except Exception:
                try:
                    cached = torch.load(path, map_location="cpu")
                except Exception:
                    logger.exception("实时口型音色缓存读取失败: %s", path)
                    continue
            if not isinstance(cached, dict) or spk_id not in cached:
                logger.warning(
                    "缓存 %s 中没有音色 %r（可用: %s）",
                    path,
                    spk_id,
                    sorted(cached) if isinstance(cached, dict) else type(cached).__name__,
                )
                continue

            entry = dict(cached[spk_id])
            if not _has_endofprompt(entry):
                _ensure_endofprompt_prefix(model, entry, spk_id)
            if not _has_endofprompt(entry):
                logger.error(
                    "音色 %s 缺少 <|endofprompt|> 且无法补全，回退固定音色", spk_id
                )
                return None

            frontend.spk2info[spk_id] = entry
            _save_spkinfo(model, spk_id)
            logger.info("已从 %s 导入实时口型音色: %s", path, spk_id)
            return spk_id
    return None


def _save_spkinfo(model, spk_id: str) -> None:
    try:
        model.save_spkinfo()
    except Exception:
        logger.exception(
            "音色 %s 写入 %s 失败（本次运行仍可用）",
            spk_id,
            _resolve_model_dir(getattr(model, "model_dir", "models/Fun-CosyVoice3-0.5B"))
            / "spk2info.pt",
        )


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _live_ref_path(opt) -> Path:
    raw = getattr(opt, "live_ref_file", "") or str(LIVE_REF_FILE_DEFAULT)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


def _live_ref_text(opt) -> str:
    return (getattr(opt, "live_ref_text", "") or LIVE_REF_TEXT_DEFAULT).strip()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_marker(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _ensure_ref_speaker(
    model,
    spk_id: str,
    audio_path: Path,
    ref_text: str,
    model_dir,
) -> str:
    """用参考音频注册零样本音色并按 sha256 缓存（投递音色与训练音色共用）。

    注册前先裁掉参考音频的首尾静音（见 ``_prepared_ref_audio``）；marker
    里记的是**裁剪后**音频的 sha256，因此裁剪参数一改就会自动重注册。
    """
    prepared = _prepared_ref_audio(audio_path)
    digest = _file_sha256(prepared)
    marker_path = LIVE_SPK_MARKER_DIR / f"{spk_id}.json"
    resolved_model_dir = str(_resolve_model_dir(model_dir))
    marker = _read_marker(marker_path)
    with _model_lock:
        if (
            spk_id in model.list_available_spks()
            and marker.get("ref_sha256") == digest
            and marker.get("ref_text") == ref_text
            and marker.get("model_dir") == resolved_model_dir
        ):
            logger.info(
                "复用已注册音色 %s（参考音频 %s 未变）",
                spk_id,
                _display_path(audio_path),
            )
            return spk_id

        logger.info(
            "用参考音频注册音色 %s: %s（%s）",
            spk_id,
            _display_path(prepared),
            ref_text,
        )
        started = time.perf_counter()
        model.add_zero_shot_spk(
            f"{COSYVOICE3_PROMPT_PREFIX}{ref_text}", str(prepared), spk_id
        )
        model.save_spkinfo()
        logger.info(
            "音色 %s 注册完成: %.2fs",
            spk_id,
            time.perf_counter() - started,
        )

    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps(
                {
                    "spk_id": spk_id,
                    "ref_file": _display_path(prepared),
                    "source_file": _display_path(audio_path),
                    "ref_text": ref_text,
                    "ref_sha256": digest,
                    "model_dir": resolved_model_dir,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.exception("音色标记写入失败: %s", marker_path)
    return spk_id


def _ensure_live_ref_speaker(model, model_dir, opt=None) -> str | None:
    """用参考音频注册实时口型音色；音频或文本变了就重新注册。

    旧流程写在 data/avatars/*/spk2info.pt 的音色缓存来自另一版 speech
    tokenizer，码本映射不同（实测与当前分词器 0% 逐位一致），直接使用会让
    音色走样；因此实时口型一律走这里：以参考音频为准，用当前模型重新提取
    prompt_text / speech token / mel / embedding。
    """
    audio_path = _live_ref_path(opt)
    ref_text = _live_ref_text(opt)
    spk_id = getattr(opt, "live_spk_id", "") or LIVE_SPK_ID_DEFAULT
    if not spk_id:
        return None
    if not audio_path.is_file():
        logger.warning("实时口型参考音频不存在: %s", _display_path(audio_path))
        return None
    if not ref_text:
        logger.warning("实时口型参考音频缺少对应文本（--live_ref_text），跳过注册")
        return None
    return _ensure_ref_speaker(model, spk_id, audio_path, ref_text, model_dir)


def _drop_voice_speaker(model, model_dir) -> str | None:
    """界面切换的音色（server/assets 管理），优先级最高。

    两类音色：``ref``（data/drop 的 wav+txt，用当前模型重新注册，音色最准）
    与 ``legacy``（旧流程的 spk2info.pt 训练缓存，导入时补 <|endofprompt|>）。
    失败时静默回退默认训练音色链路，说话永不中断。
    """
    try:
        from server import assets

        info = assets.active_voice_info()
    except Exception:
        return None
    if info is None:
        return None
    try:
        if info.get("type") == "legacy":
            return _import_voice_cache(
                model,
                Path(info["path"]),
                source_spk=info["source_spk"],
                target_spk=info["target_spk"],
            )
        return _ensure_ref_speaker(
            model, info["spk_id"], Path(info["path"]), info["text"], model_dir
        )
    except Exception:
        logger.exception("界面音色 %r 注册失败，回退默认音色", info.get("spk_id") or info.get("target_spk"))
        return None


def _import_voice_cache(
    model,
    cache_path: Path,
    source_spk: str,
    target_spk: str,
) -> str | None:
    """把旧流程 spk2info.pt 里的训练音色导入当前模型（改挂到 target_spk 名下）。

    与 ``_import_live_speaker`` 同一套修法：缓存缺 <|endofprompt|> 前缀就补上，
    补不齐宁可不用。导入结果持久化进模型目录的 spk2info.pt，之后秒级复用。
    """
    with _model_lock:
        if target_spk in model.list_available_spks():
            return target_spk
        try:
            cached = torch.load(cache_path, map_location="cpu", weights_only=True)
        except Exception:
            try:
                cached = torch.load(cache_path, map_location="cpu")
            except Exception:
                logger.exception("遗留音色缓存读取失败: %s", cache_path)
                return None
        if not isinstance(cached, dict) or not cached:
            logger.warning("遗留音色缓存为空: %s", cache_path)
            return None
        if source_spk not in cached:
            if len(cached) == 1:
                source_spk = next(iter(cached))  # 缓存里只有一个音色，名字对不上也用它
            else:
                logger.warning(
                    "缓存 %s 中没有音色 %r（可用: %s）",
                    cache_path, source_spk, sorted(cached),
                )
                return None
        entry = dict(cached[source_spk])
        if not _has_endofprompt(entry):
            _ensure_endofprompt_prefix(model, entry, target_spk)
        if not _has_endofprompt(entry):
            logger.error("遗留音色 %s 缺少 <|endofprompt|> 且无法补全，放弃使用", target_spk)
            return None
        frontend = getattr(model, "frontend", None)
        if frontend is None or not hasattr(frontend, "spk2info"):
            logger.error("模型缺少 frontend.spk2info，无法导入遗留音色")
            return None
        frontend.spk2info[target_spk] = entry
        _save_spkinfo(model, target_spk)
        logger.info("已导入遗留训练音色: %s -> %s", cache_path.name, target_spk)
        return target_spk


def _live_speaker_id(model, model_dir, opt=None) -> str:
    """实时口型模式的音色：界面投递音色 > 参考音频注册 > 旧缓存搬运 > 固定音色。"""
    dropped = _drop_voice_speaker(model, model_dir)
    if dropped is not None:
        return dropped
    registered = _ensure_live_ref_speaker(model, model_dir, opt)
    if registered is not None:
        return registered

    spk_id = getattr(opt, "live_spk_id", "") or LIVE_SPK_ID_DEFAULT
    avatar_id = getattr(opt, "avatar_id", "") or ""
    cache_path = getattr(opt, "live_spk_cache", "") or ""
    logger.warning(
        "参考音频不可用，尝试搬运旧音色缓存 %r（可能来自另一版分词器，音色不保证）",
        spk_id,
    )
    imported = _import_live_speaker(
        model, spk_id, avatar_id=avatar_id, cache_path=cache_path
    )
    if imported is not None:
        return imported

    logger.warning(
        "未找到实时口型音色 %r（查找位置 %s），回退项目固定音色 %s",
        spk_id,
        [_display_path(p) for p in _avatar_spk_cache_candidates(avatar_id, cache_path)],
        FIXED_SPEAKER_ID,
    )
    return _prepare_fixed_voice(model_dir)


def _persona_speaker_id(model) -> str | None:
    """Zero-shot speaker bound to the active persona, extracted once and cached.

    Personas without a usable voice reference keep the project fixed voice.
    """
    try:
        from server.personas import get_active_persona

        persona = get_active_persona()
    except Exception:
        return None
    if persona is None or not persona.has_voice:
        return None
    audio_path = ROOT / persona.ref_wav
    if not audio_path.is_file():
        logger.warning(
            "人设 %s 的参考音频不存在: %s，回退固定音色", persona.id, audio_path
        )
        return None
    # spk_id 用**裁剪后**音频的内容摘要：裁剪参数一变 id 就变，旧注册自动失效
    prepared = _prepared_ref_audio(audio_path)
    digest = _file_sha256(prepared)[:8]
    spk_id = f"persona_{persona.id}_{digest}"
    with _model_lock:
        if spk_id in model.list_available_spks():
            return spk_id
        logger.info("首次提取人设音色 %s: %s", persona.id, _display_path(prepared))
        model.add_zero_shot_spk(
            f"{COSYVOICE3_PROMPT_PREFIX}{persona.ref_text}", str(prepared), spk_id
        )
        model.save_spkinfo()
    return spk_id


def _ensure_instruct_speaker(model, persona) -> str | None:
    """Create (once) a cached speaker for an instruct persona.

    Two reasons this cannot run per request: the frontend drops the
    instruction when a speaker id is given, and instruct2 synthesis is far
    slower than the cached zero-shot path. So we synthesize one short sample
    with instruct2 (with the mandatory <|endofprompt|> prefix), register it
    as a regular zero-shot speaker and persist it in spk2info.pt. Every
    later request uses the fast cached path.
    """
    if not persona.has_instruct_voice or persona.has_voice:
        return None
    spk_id = f"instruct_{persona.id}"
    with _model_lock:
        if spk_id in model.list_available_spks():
            return spk_id

        # 注册样本只作为音色参考，不会被念给用户听；这里不写任何人设名字，
        # 避免人设改名后音色缓存语义对不上（已有缓存不受影响）。
        sample_text = "你好呀，今天过得怎么样？有什么想聊的都可以告诉我。"
        prompt_text = f"{COSYVOICE3_PROMPT_PREFIX}{persona.instruct}"
        logger.info("首次生成人设音色 %s（instruct2，一次性）", spk_id)
        chunks = []
        collected = 0
        needed = int(model.sample_rate * 6)
        started = time.perf_counter()
        for item in model.inference_instruct2(
            sample_text,
            prompt_text,
            str(FIXED_REFERENCE_AUDIO),
            stream=True,
        ):
            speech = item["tts_speech"].detach().float().cpu().numpy().reshape(-1)
            chunks.append(speech)
            collected += speech.size
            if collected >= needed:
                break
        if not chunks:
            logger.warning("人设音色生成失败：%s 未产生音频", persona.id)
            return None
        sample = _trim_after_first_pause(np.concatenate(chunks), model.sample_rate)
        if sample.size < model.sample_rate:
            logger.warning("人设音色样本过短：%s", persona.id)
            return None
        sample_path = ROOT / "mp4" / f"persona_{persona.id}_sample.wav"
        import soundfile as sf

        sf.write(str(sample_path), sample, model.sample_rate)
        model.add_zero_shot_spk(
            f"{COSYVOICE3_PROMPT_PREFIX}{sample_text}",
            str(sample_path),
            spk_id,
        )
        model.save_spkinfo()
        logger.info(
            "人设音色 %s 已缓存（%.1fs 样本）: %.2fs",
            spk_id,
            collected / model.sample_rate,
            time.perf_counter() - started,
        )
        return spk_id


def _active_persona(mode: str | None = None):
    try:
        from server.personas import get_active_persona

        return get_active_persona(mode)
    except Exception:
        return None


def warm_up(
    model_dir: str | Path = "models/Fun-CosyVoice3-0.5B",
    opt=None,
) -> None:
    """Preload the shared model and both production voices.

    Without this the very first reply pays the whole model load (~20s). The
    live lip-sync trained voice (``live_spk``) and 暖暖's 云霞卡通 speaker
    (``guide_boy_v1``, shared by the guide page and the lightweight page)
    are both prepared here, then the production voice is exercised once so
    CUDA kernels are warm for the first real reply. Safe to run in a
    background thread; the module-level lock serializes it with any request
    that arrives mid-load.
    """
    global _warm_state, _warm_error
    model = _load_model(model_dir)
    _warm_state = "loading"
    try:
        speaker_id = _live_speaker_id(model, model_dir, opt)
        _ensure_guide_boy_speaker(model)
        started = time.perf_counter()
        with _model_lock:
            for _item in model.inference_zero_shot(
                "你好。",
                "",
                "",
                zero_shot_spk_id=speaker_id,
                stream=True,
                speed=1.0,
            ):
                break
    except Exception as exc:
        # 模型可能其实能用（例如仅试推失败），标记 failed 让前端放行并提示
        _warm_state = "failed"
        _warm_error = str(exc)
        raise
    _warm_state = "ready"
    _warm_error = ""
    logger.info(
        "CosyVoice3 预热完成: %.2fs（实时口型音色 %s）",
        time.perf_counter() - started,
        speaker_id,
    )


def _ensure_guide_boy_speaker(model) -> str:
    """暖暖的云霞卡通音色（cached，一次性注册）。

    长样本（mp4/guide_boy_ref.wav，11.8 秒）零样本克隆；样本缺失时抛错，
    由上层 TTS 重试链回退本地 Windows 语音保底（v5 旧男孩音色已删除，
    2026-10-24 用户拍板：暖暖只有云霞卡通一个音色）。
    """
    with _model_lock:
        if GUIDE_BOY_SPK_ID in model.list_available_spks():
            return GUIDE_BOY_SPK_ID
        if not GUIDE_BOY_REF_WAV.is_file():
            raise RuntimeError(
                f"云霞卡通音色样本缺失: {GUIDE_BOY_REF_WAV}（暖暖唯一音色，无替代）"
            )
        started = time.perf_counter()
        prepared = _prepared_ref_audio(GUIDE_BOY_REF_WAV)
        logger.info("注册暖暖云霞卡通音色 %s（样本 %s）", GUIDE_BOY_SPK_ID, _display_path(prepared))
        model.add_zero_shot_spk(
            f"{COSYVOICE3_PROMPT_PREFIX}{GUIDE_BOY_REF_TEXT}",
            str(prepared),
            GUIDE_BOY_SPK_ID,
        )
        model.save_spkinfo()
        logger.info("暖暖云霞卡通音色已缓存: %.2fs", time.perf_counter() - started)
        return GUIDE_BOY_SPK_ID


@register("tts", "cosyvoice3")
class CosyVoice3TTS(BaseTTS):
    """Fun-CosyVoice3 streaming zero-shot voice cloning backend."""

    def __init__(self, opt, parent, default_page_voice: bool = False):
        super().__init__(opt, parent)
        self.model_dir = getattr(
            opt, "cosyvoice3_model_dir", "models/Fun-CosyVoice3-0.5B"
        )
        self.speed = float(getattr(opt, "tts_speed", 1.0))
        # True = 暖暖（轻量页/导览页）音色链路 → 云霞卡通 guide_boy_v1；
        # False = 实时口型的训练音色链路
        self.default_page_voice = default_page_voice

    def txt_to_audio(self, msg: tuple[str, dict]):
        self.stream_tts(self._generate(msg[0], msg[1] or {}), msg)

    def _generate(self, text: str, event: dict | None = None) -> Iterator[np.ndarray]:
        model = _load_model(self.model_dir)
        lock_queued = time.perf_counter()
        first_chunk = True
        with _model_lock:
            lock_wait = time.perf_counter() - lock_queued
            started = time.perf_counter()
            # 音色来源：live 模式导览页逐段带 event.voice="boy"；
            # default 模式（导览页/轻量页）实例本身 default_page_voice=True。
            # 两者都解析为暖暖唯一音色——云霞卡通 guide_boy_v1。
            voice_tag = event.get("voice") if isinstance(event, dict) else ""
            if voice_tag == "boy" or self.default_page_voice:
                # 暖暖（guide 人设/轻量页）：云霞卡通音色（2026-10-24 用户
                # 拍板：旧默认页男孩音色 v5 已删除，全线只用云霞卡通）
                speaker_id = _ensure_guide_boy_speaker(model)
            else:
                # 实时口型模式的人设（personas/*.md 里 mode: live）
                persona = _active_persona("live")
                speaker_id = None
                if persona is not None:
                    speaker_id = _ensure_instruct_speaker(model, persona)
                    if speaker_id is None:
                        speaker_id = _persona_speaker_id(model)
                if speaker_id is None:
                    # 人设没有专属音色时使用实时口型的训练音色
                    # （data/avatars/<avatar_id>/spk2info.pt 里的 live_spk）。
                    # 这里不再回退轻量页童声：口型模式必须是成人的训练音色，
                    # 童声只服务于轻量页（default_page_voice=True）。
                    speaker_id = _live_speaker_id(model, self.model_dir, self.opt)
            # zero-shot 续写模式：cross-lingual 会被 CosyVoice3 的
            # <|endofprompt|> 断言拒绝（prompt_text 被删、llm_job 崩溃、
            # 流式循环挂死），不可用。短句幻觉问题靠短注册样本 +
            # 调大首段下限缓解（见 HANDOFF_FIXES.md P0-B）。
            stream = model.inference_zero_shot(
                text,
                "",
                "",
                zero_shot_spk_id=speaker_id,
                stream=True,
                speed=self.speed,
            )
            for item in stream:
                if self.state != State.RUNNING:
                    return
                speech = item["tts_speech"].detach().float().cpu().numpy().reshape(-1)
                if speech.size == 0:
                    continue
                if first_chunk:
                    logger.info(
                        "CosyVoice3 首包延迟: %.3fs（音色 %s，等锁 %.3fs）",
                        time.perf_counter() - started,
                        speaker_id,
                        lock_wait,
                    )
                    first_chunk = False
                if model.sample_rate != self.sample_rate:
                    import resampy

                    speech = resampy.resample(
                        speech,
                        sr_orig=model.sample_rate,
                        sr_new=self.sample_rate,
                    )
                yield np.asarray(speech, dtype=np.float32)
        if first_chunk and self.state == State.RUNNING:
            # 走到这里说明流式循环正常结束却一段音频都没有：通常是上游
            # llm_job 抛异常（例如 <|endofprompt|> 断言）。明确报出来，
            # 不要让上层只看到"没有声音"。
            logger.warning(
                "CosyVoice3 未产出任何音频（音色 %s，文本 %.20r），"
                "请检查上游 llm_job 是否崩溃",
                speaker_id,
                text,
            )
        logger.info("CosyVoice3 合成完成: %.2fs", time.perf_counter() - started)

    def stream_tts(
        self,
        audio_stream: Iterator[np.ndarray],
        msg: tuple[str, dict],
    ) -> None:
        text, event = msg
        first = True
        carry = np.empty(0, dtype=np.float32)
        for pcm in audio_stream:
            if self.state != State.RUNNING:
                return
            pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
            if carry.size:
                pcm = np.concatenate((carry, pcm))
            index = 0
            remaining = pcm.size
            while remaining >= self.chunk:
                if self.state != State.RUNNING:
                    return
                eventpoint = dict(event)
                if first:
                    eventpoint.update(status="start", text=text)
                    first = False
                self.parent.put_audio_frame(
                    pcm[index : index + self.chunk],
                    eventpoint,
                )
                index += self.chunk
                remaining -= self.chunk
            carry = pcm[index:].copy()

        if self.state != State.RUNNING:
            return
        final_chunk = np.zeros(self.chunk, dtype=np.float32)
        final_chunk[: carry.size] = carry
        eventpoint = dict(event)
        eventpoint.update(status="end", text=text)
        self.parent.put_audio_frame(final_chunk, eventpoint)
