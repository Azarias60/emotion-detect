# -*-coding: utf-8 -*-
"""命令行与 ASGI 兼容入口：python app_api.py / uvicorn app_api:app。"""
import os

from application import create_app
from core.logger import get_logger

logger = get_logger(__name__)
app = create_app()


def free_port_if_occupied(port: int = 8081):
    """检测并自动清理占用目标端口的历史残留进程，避免 WinError 10048 端口冲突。"""
    import subprocess
    import time

    current_pid = os.getpid()
    try:
        cmd = "netstat -ano -p tcp"
        output = subprocess.check_output(cmd, shell=True, text=True, errors="ignore")
        pids = set()
        for line in output.splitlines():
            line = line.strip()
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                if len(parts) >= 5 and parts[-1].isdigit():
                    pid = int(parts[-1])
                    if pid != current_pid and pid > 0:
                        pids.add(pid)

        for pid in pids:
            logger.warning("检测到端口 %s 被旧残留进程 (PID: %s) 占用，正在自动清理释放...", port, pid)
            subprocess.run(f"taskkill /F /T /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.5)
    except Exception as e:
        logger.warning("自动端口检测跳过: %s", e)


# --- 7. 服务启动 ---
if __name__ == "__main__":
    import uvicorn

    # 启动前自愈：如果上次非正常关闭残留了 8081 占用，自动清理
    free_port_if_occupied(8081)

    try:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8081,
            timeout_graceful_shutdown=2,
        )
    except KeyboardInterrupt:
        logger.info("收到键盘退出信号，后端服务已安全退出。")
