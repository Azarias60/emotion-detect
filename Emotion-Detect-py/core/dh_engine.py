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
import time
from pathlib import Path

from core.logger import get_logger

logger = get_logger(__name__)

# core/dh_engine.py → parents[2] = 仓库根 emotionDetect/
ENGINE_DIR = Path(__file__).resolve().parents[2] / "digital-human" / "livetalking"
ENGINE_PORT = 8020

# 与 run-8020.bat 保持一致的工作参数
ENGINE_ARGS = [
    "app.py", "--transport", "webrtc", "--model", "wav2lip",
    "--avatar_id", "e2etest01", "--tts", "cosyvoice3",
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


def _engine_pid_by_port() -> int | None:
    """8020 监听进程的 PID。

    后端被强杀时 atexit 不跑，它拉起的引擎成为孤儿：新后端的 ``_proc`` 是
    None，但端口还在听、显存还被占。制作让位必须能停掉这种孤儿，否则
    SCAIL/LTX 照样和它抢 16G 显存。按端口找回再收回。
    """
    import re
    import subprocess as sp

    try:
        # netstat 在中文 Windows 是 GBK 输出，text=True 默认 utf-8 会解码失败
        # （reader 线程抛 UnicodeDecodeError，stdout 变 None）。按字节收再
        # 容错解码——只用 ASCII 的 LISTENING 行，乱码行无所谓。
        raw = sp.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                     timeout=15, creationflags=sp.CREATE_NO_WINDOW).stdout
        out = raw.decode("utf-8", errors="replace")
    except Exception as error:
        logger.warning("查询 8020 监听进程失败：%s", error)
        return None
    for line in out.splitlines():
        if f":{ENGINE_PORT} " in line and "LISTENING" in line:
            match = re.search(r"(\d+)\s*$", line)
            if match:
                return int(match.group(1))
    return None


def _kill_pid(pid: int) -> bool:
    """结束指定进程**树**（先 terminate 后 kill）。

    必须带 ``/T``：引擎是两级结构——venv 的 python.exe 拉起后，真正干活的
    worker 是它再起的 Anaconda python（监听 8020）。只杀父进程时 Windows 不
    连带杀子进程，worker 继续占着端口，于是 start_engine 看到"8020 已在监听"
    直接跳过重启——**改过的引擎代码永远不生效**（2026-11-13 实锤：连续三次
    stop/start 之后，引擎进程创建时间仍是三小时前那一次）。
    """
    import subprocess as sp

    for args in (["/PID", str(pid), "/T"], ["/PID", str(pid), "/T", "/F"]):
        try:
            sp.run(["taskkill", *args], capture_output=True, timeout=15,
                   creationflags=sp.CREATE_NO_WINDOW)
        except Exception as error:
            logger.warning("结束进程 %s 失败：%s", pid, error)
            return False
    return True


def stop_engine() -> bool:
    """停数字人引擎；返回 True=本次确实停掉了一个进程。

    两类对象：本模块拉起的 ``_proc``，以及后端强杀后遗留的孤儿（按 8020
    端口找回）。外部手动启动的实例同样会被停——制作让位需要显存，这点
    优先于"不碰外部实例"（停止前日志会写明来源）。
    """
    global _proc
    proc = _proc
    orphan_pid = None
    if proc is None:
        orphan_pid = _engine_pid_by_port()
        if orphan_pid is None:
            return False
        logger.info("发现后端重启遗留的孤儿引擎（PID %s，8020 仍在监听），收回", orphan_pid)
    try:
        pid = proc.pid if proc is not None else orphan_pid
        if proc is not None:
            # 先温柔 terminate 父进程，再按端口收尾子 worker（进程树）
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            _kill_pid(pid)
        else:
            _kill_pid(orphan_pid)
        # 收尾校验：8020 必须真的释放。worker 子进程若还活着，端口仍在监听，
        # start_engine 会误判"已在运行"直接跳过——用户改的代码永不生效。
        for _ in range(24):  # 最多等 12 秒
            leftover = _engine_pid_by_port()
            if leftover is None:
                break
            logger.info("8020 仍被 PID %s 占用，继续结束进程树", leftover)
            _kill_pid(leftover)
            time.sleep(0.5)
        if _engine_pid_by_port() is None:
            logger.info("数字人引擎（PID %s）已停止，8020 已释放", pid)
            return True
        logger.warning("数字人引擎停止失败：8020 仍被占用")
        return False
    except Exception as error:
        logger.warning("停止数字人引擎失败：%s", error)
        return False
    finally:
        _proc = None
