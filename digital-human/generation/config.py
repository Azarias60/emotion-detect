# -*- coding: utf-8 -*-
"""video-gen 全局路径配置。整机迁移只改这一个文件。

目录约定（2026-10-20 整理后）：本文件在 generation/ 下，管「段库生成工具链」
自己的 recipes/workflows；runs/ 与 assets/ 是跨工具链共享的（studio 生产线也用），
留在 digital-human/ 根。
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))   # generation/
DH_ROOT = os.path.dirname(PROJECT_ROOT)                     # digital-human/
RUNS_DIR = os.path.join(DH_ROOT, "runs")
RECIPES_DIR = os.path.join(PROJECT_ROOT, "recipes")
WORKFLOWS_DIR = os.path.join(PROJECT_ROOT, "workflows")
ASSETS_DIR = os.path.join(DH_ROOT, "assets")

# ---- ComfyUI 便携版（API 端口 8188）----
COMFY_ROOT = r"E:\Comfyui\ComfyUI\ComfyUI-Main\ComfyUIportable\ComfyUIportable\ComfyUI"
COMFY_INPUT = os.path.join(COMFY_ROOT, "input")
COMFY_OUTPUT = os.path.join(COMFY_ROOT, "output")
SERVER = "http://127.0.0.1:8188"

# ---- 参考图（SCAIL 节点 30 LoadImage 的文件名，须存在于 ComfyUI input）----
REF_IMAGE = "5186E77AABC71A6BD18789DF2625EDA3.jpg"

# ---- 出片规格：全部原生 704x1280@20fps，不做任何归一化/缩放/帧率转换 ----
W, H, FPS = 704, 1280, 20

# ---- 交付目录（桌面验收目录，可被 --out 覆盖）----
DESKTOP_DELIVERY = os.path.join(os.path.expanduser("~"), "Desktop",
                                "动作替换验证", "目标_idle过渡greet")


def ffmpeg_exe():
    """ffmpeg 可执行文件：优先 imageio-ffmpeg 自带二进制，不依赖 LiveTalking venv。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return (r"E:\LiveTalking\LiveTalking\venv\Lib\site-packages"
                r"\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe")
