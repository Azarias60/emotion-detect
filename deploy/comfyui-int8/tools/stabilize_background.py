# -*- coding: utf-8 -*-
"""动作段静态背景锁定：消除 SCAIL 逐帧重绘带来的背景抖动。

原理：SCAIL 姿态迁移逐帧重绘整幅画面，驱动视频里的微小相机/身体运动被
一并搬过来 → 背景持续微晃（2026-11-05 实测：动作段背景中位帧差 1.5~2.6，
过渡段仅 0.15~1.1）。本工具对每段：
  1) 逐像素中位得到"背景板"（人物只占中段，边缘像素多数帧是纯背景）；
  2) 用帧与背景板差 > 阈值 + 膨胀 + 羽化，得到人物掩膜；
  3) 掩膜外整块替换成背景板 → 背景零抖动；掩膜内保留原帧（动作/表情不变）。

用法：
  livetalking/venv/Scripts/python.exe studio/stabilize_background.py \
      --segments <dir> [--apply] [--only-action] [--th 18] [--feather 4]
"""
import argparse
import subprocess
from pathlib import Path

import cv2
import numpy as np

FFMPEG = str(Path(__file__).resolve().parents[1] /
             "livetalking/venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe")


def find_ffmpeg() -> str:
    cand = Path(FFMPEG)
    if cand.is_file():
        return str(cand)
    import glob
    hits = glob.glob(str(Path(__file__).resolve().parents[1] /
                         "livetalking/venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg*.exe"))
    if hits:
        return hits[0]
    raise SystemExit("找不到 ffmpeg")


def load(p):
    cap = cv2.VideoCapture(str(p))
    fr = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        fr.append(f)
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    cap.release()
    return fr, fps


def save(p, fr, fps, ffmpeg):
    h, w = fr[0].shape[:2]
    tmp = Path(str(p) + ".tmp.mp4")
    cmd = [ffmpeg, "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "16",
           "-pix_fmt", "yuv420p", str(tmp)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    for f in fr:
        proc.stdin.write(f.astype(np.uint8).tobytes())
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0 or not tmp.is_file():
        raise SystemExit(f"ffmpeg 写盘失败: {p}")
    tmp.replace(p)


def bg_jitter(fr):
    """掩膜外的背景帧差中位数（越小越稳）。用整幅的固定边界带近似背景。"""
    g = [cv2.cvtColor(cv2.resize(f, (352, 640)), cv2.COLOR_BGR2GRAY).astype(np.float32) for f in fr]
    band = np.zeros(g[0].shape, bool)
    band[:90, :] = True       # 顶部背景带
    band[:, :55] = True       # 左侧背景带
    band[:, -55:] = True      # 右侧背景带
    ds = [float(np.abs(g[i][band] - g[i - 1][band]).mean()) for i in range(1, len(g))]
    return float(np.median(ds)) if ds else 0.0


def stabilize(p: Path, apply: bool, th: int, feather: int, ffmpeg: str, passes: int = 2,
              static_mask: bool = False):
    fr, fps = load(p)
    if len(fr) < 5:
        return None
    before = bg_jitter(fr)
    k_open = np.ones((3, 3), np.uint8)
    k_dil = np.ones((9, 9), np.uint8)
    cur = fr
    for _ in range(max(1, passes)):
        # 背景板：逐像素中位（第二轮起输入已是锁定过背景的帧，板更干净）
        stack = np.stack([f.astype(np.float32) for f in cur])
        plate = np.median(stack, axis=0)
        gray_plate = cv2.cvtColor(plate.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
        masks = []
        for f in cur:
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
            mask = (np.abs(g - gray_plate) > th).astype(np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)
            mask = cv2.dilate(mask, k_dil, iterations=1)
            n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
            if n > 1:
                idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
                x, y = stats[idx, cv2.CC_STAT_LEFT], stats[idx, cv2.CC_STAT_TOP]
                w_, h_ = stats[idx, cv2.CC_STAT_WIDTH], stats[idx, cv2.CC_STAT_HEIGHT]
                keep = (lab == idx).astype(np.uint8)
                keep[max(0, y - int(h_ * 0.5)):y, x:x + w_] = 1
                mask = cv2.dilate(keep, k_dil, iterations=1)
            masks.append(mask)
        if static_mask:
            # 整段并集 + 再膨胀：掩膜边界固定 → 边界不闪，背景可整块锁定
            union = np.zeros_like(masks[0])
            for m in masks:
                union |= m
            union = cv2.dilate(union, np.ones((15, 15), np.uint8), iterations=1)
            mask_list = [union] * len(cur)
        else:
            mask_list = masks
        out = []
        H, W = cur[0].shape[:2]
        margin = max(8, int(W * 0.025))  # 左右/顶部强制留白（人物不触边）
        for f, m in zip(cur, mask_list):
            alpha = cv2.GaussianBlur(m.astype(np.float32), (0, 0), feather)
            # 画面左右与顶部边缘强制用背景板（2026-11-05 视觉核验实测：头部
            # 延伸矩形会吃到画面边缘，导致边缘带保留了会晃的原始像素）
            alpha[:, :margin] = 0
            alpha[:, -margin:] = 0
            alpha[:margin, :] = 0
            alpha = alpha[..., None]
            comp = f.astype(np.float32) * alpha + plate * (1 - alpha)
            out.append(np.clip(comp, 0, 255).astype(np.uint8))
        cur = out
    after = bg_jitter(cur)
    print(f"{p.name:22s} {len(fr)}帧 背景抖动 {before:.2f} -> {after:.2f}"
          + ("  [已写回]" if apply else "  [dry-run]"))
    if apply:
        save(p, cur, fps, ffmpeg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only-action", action="store_true",
                    help="只处理动作段（不含 ' to ' 的 mp4）")
    ap.add_argument("--th", type=int, default=18)
    ap.add_argument("--feather", type=int, default=4)
    ap.add_argument("--passes", type=int, default=2)
    args = ap.parse_args()
    ffmpeg = find_ffmpeg()
    for p in sorted(Path(args.segments).glob("*.mp4")):
        if args.only_action and " to " in p.stem:
            continue
        stabilize(p, args.apply, args.th, args.feather, ffmpeg, args.passes)


if __name__ == "__main__":
    main()
