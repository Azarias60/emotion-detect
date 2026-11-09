"""网页驱动的服务器生命周期：页面在 → 服务在，页面关 → 服务退。

桌面快捷方式（launcher.ps1）以 ``LIVETALKING_AUTOEXIT=1`` 启动本服务时启用：

- 页面每 5 秒 POST ``/api/heartbeat``；关闭时用 sendBeacon POST ``/api/bye``。
- 看门狗线程收到 bye（或心跳断流超过 ``LIVETALKING_AUTOEXIT_IDLE`` 秒）后
  按进程树结束服务——venv 的 python.exe 往往只是父进程，真正监听端口的
  是它拉起的子进程，只退自己会留下占着端口的孤儿。

epoch（本进程的启动时间戳）用来区分"新旧两代服务"：快捷方式重开服务后，
旧标签页的心跳会打到新进程上。旧页面发现 epoch 变了就自行停止心跳，
它发的 bye 也因 epoch 不匹配被忽略，不会误杀新服务。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

from utils.logger import logger

_EPOCH = time.time()
_last_seen = 0.0
_seen = False  # 至少收到过一次心跳（说明有页面打开过）
_bye = False
_lock = threading.Lock()

_IDLE_SEC = float(os.environ.get("LIVETALKING_AUTOEXIT_IDLE", "120"))
_POLL_SEC = 2.0


def epoch() -> float:
    return _EPOCH


def enabled() -> bool:
    return os.environ.get("LIVETALKING_AUTOEXIT") == "1"


def mark_seen() -> None:
    global _last_seen, _seen
    with _lock:
        _last_seen = time.time()
        _seen = True


def mark_bye() -> None:
    # bye 本身就证明页面打开过，一并置 _seen，兼容"开了立刻关"的极端情况
    global _bye, _seen
    with _lock:
        _bye = True
        _seen = True


def should_exit(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    with _lock:
        if _bye:
            return True
        return _seen and (now - _last_seen) > _IDLE_SEC


def maybe_start_watcher() -> None:
    """仅当环境变量开启时启动看门狗（开发用 restart3 启动则不自动退出）。"""
    if not enabled():
        return

    def _watch() -> None:
        while True:
            time.sleep(_POLL_SEC)
            if should_exit():
                logger.info("数字人网页已关闭（或心跳断流 %.0fs），服务器自动退出", _IDLE_SEC)
                _kill_process_tree()
                return

    threading.Thread(target=_watch, name="autoexit-watcher", daemon=True).start()


def _kill_process_tree() -> None:
    # taskkill /T 连同子进程一起结束；杀自己时命令能正常完成
    try:
        subprocess.Popen(
            ["taskkill", "/F", "/T", "/PID", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        logger.exception("自动退出失败，请手动结束 python 进程")


def _reset_for_tests() -> None:
    """测试专用：还原全部状态。"""
    global _last_seen, _seen, _bye
    with _lock:
        _last_seen = 0.0
        _seen = False
        _bye = False
