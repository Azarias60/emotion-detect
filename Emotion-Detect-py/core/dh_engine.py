"""数字人引擎（digital-human/livetalking）子进程管理。

情感数字人页面通过 WebRTC 直连 127.0.0.1:8020 的引擎进程；引擎带 GPU 模型
与自有事件循环，必须独立成进程，但**生命周期归应用管**——后端启动时若
8020 未在监听就静默拉起（LIVETALKING_NO_BROWSER=1，绝不弹浏览器窗口），
后端退出时只回收自己拉起的那个进程。用户手动启动的实例（端口已被占用）
不会被碰。

环境开关：`DETECT_DH_ENGINE=0` 关闭自动拉起（调试引擎本身时用）。
"""
import atexit
import os
import subprocess
from pathlib import Path

from core.logger import get_logger

logger = get_logger(__name__)

# core/dh_engine.py → parents[2] = 仓库根 emotionDetect/
ENGINE_DIR = Path(__file__).resolve().parents[2] / "digital-human" / "livetalking"
ENGINE_PORT = 8020

# 与 run-8020.bat 保持一致的工作参数
ENGINE_ARGS = [
    "app.py", "--transport", "webrtc", "--model", "wav2lip",
    "--avatar_id", "my_avatar", "--tts", "cosyvoice3",
    "--listenport", "8020", "--batch_size", "4",
    "--webrtc_video_queue_size", "6", "--webrtc_audio_queue_size", "12",
    # 语速 1.1：听感基本无差、播放时长省 ~9%，缩短整轮对话（2026-10-23 用户反馈对话偏慢）
    "--tts_speed", "1.1",
]

_proc: subprocess.Popen | None = None


def _port_listening(port: int, timeout: float = 1.0) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_engine() -> bool:
    """8020 没有实例在跑时静默拉起引擎；返回是否由本次调用启动。"""
    global _proc
    if _port_listening(ENGINE_PORT):
        logger.info("数字人引擎已在运行（8020 已监听），不重复启动")
        return False
    python = ENGINE_DIR / "venv" / "Scripts" / "python.exe"
    if not python.is_file():
        logger.warning("数字人引擎不存在（%s），情感数字人页面将不可用", python)
        return False
    env = {**os.environ, "LIVETALKING_NO_BROWSER": "1"}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        # 引擎日志落盘：DEVNULL 吞掉了所有报错（2026-10-24 排"说了没反应"时
        # 无迹可查）。追加写，按周清理交给 cleanup 脚本。
        log_path = ENGINE_DIR / "runs" / "engine.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _log = open(log_path, "ab")
        _proc = subprocess.Popen(
            [str(python), *ENGINE_ARGS],
            cwd=str(ENGINE_DIR),
            env=env,
            creationflags=flags,
            stdout=_log,
            stderr=subprocess.STDOUT,
        )
    except Exception as error:
        logger.error("数字人引擎启动失败：%s", error)
        return False
    atexit.register(stop_engine)
    logger.info("数字人引擎已随应用启动（PID %s，8020 端口，模型加载约半分钟）", _proc.pid)
    return True


def stop_engine() -> None:
    """只回收本模块拉起的引擎进程；外部启动的实例不归这里管。"""
    global _proc
    if _proc is None:
        return
    try:
        _proc.terminate()
        try:
            _proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _proc.kill()
            _proc.wait(timeout=5)
        logger.info("数字人引擎（PID %s）已随应用停止", _proc.pid)
    except Exception as error:
        logger.warning("停止数字人引擎失败：%s", error)
    finally:
        _proc = None
