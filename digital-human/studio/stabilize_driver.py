# -*- coding: utf-8 -*-
"""驱动视频横向稳定化：只做整帧平移，把驱动人物钉在同一个横坐标上。

2026-11-09 实测结论（idle1 背景/人物不稳的根因）：
  assets/materials/idle1.mp4 里真人自己左右晃了 94px（x 328→278→372），
  SCAIL 忠实照搬了这个晃动，输出 idle1 的头部横坐标同步走了 88px；
  对照组 idle2 驱动只晃 8px，输出 4.5px——模型没问题，是驱动在晃。
  人物一晃，身体边缘不断露出/遮住背景，看起来就是「背景在动、人慢慢飘」。

做法（不碰成片，只处理驱动输入）：
  驱动视频只向 SCAIL 提供姿态和掩码，它的像素**不会**出现在成片里，
  所以把驱动整帧平移到人物横坐标恒定，等价于「换一个不乱晃的演员」，
  成片依旧由模型原样生成，不裁不补不插帧。
    1. 时间中值当背景，|当前帧-背景| 取连通人体，求横坐标重心；
    2. 轨迹做中值滤波去掉检测抖动，锚点取全片中位数（平均位置不变，
       模型对参考图的构图映射也就不变）；
    3. 逐帧整帧平移 (anchor - cx)，边界 replicate 填充。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/stabilize_driver.py \
        assets/materials/idle1.mp4 --out assets/drivers_stable/idle1.mp4
    # 多个一次做：
    livetalking/venv/Scripts/python.exe studio/stabilize_driver.py \
        assets/materials/idle1.mp4 assets/materials/idle3.mp4 --out-dir assets/drivers_stable
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def read_frames(path: Path):
    cap = cv2.VideoCapture(str(path))
    frames, fps = [], cap.get(cv2.CAP_PROP_FPS) or 25.0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    if not frames:
        raise SystemExit(f"读不到帧：{path}")
    return frames, fps


def person_x(frames) -> np.ndarray:
    """逐帧人体横坐标重心（相对时间中值背景的前景掩码）。"""
    small = [cv2.resize(f, (f.shape[1] // 2, f.shape[0] // 2)) for f in frames]
    grays = np.stack([cv2.cvtColor(s, cv2.COLOR_BGR2GRAY).astype(np.float32) for s in small])
    bg = np.median(grays, axis=0)
    xs = []
    for g in grays:
        m = (np.abs(g - bg) > 18).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        m = cv2.dilate(m, np.ones((9, 9), np.uint8))
        ys, xsr = np.nonzero(m)
        xs.append(float(xsr.mean() * 2) if len(xsr) > 50 else np.nan)
    xs = np.array(xs)
    if np.isnan(xs).any():
        ok = ~np.isnan(xs)
        xs[~ok] = np.interp(np.where(~ok)[0], np.where(ok)[0], xs[ok])
    return xs


def median_filter(xs: np.ndarray, k: int = 9) -> np.ndarray:
    half = max(1, k // 2)
    out = np.empty_like(xs)
    for i in range(len(xs)):
        out[i] = np.median(xs[max(0, i - half): min(len(xs), i + half + 1)])
    return out


def write_frames(path: Path, frames, fps: float):
    h, w = frames[0].shape[:2]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp.mp4")
    out = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in frames:
        out.write(f)
    out.release()
    tmp.replace(path)


def stabilize(src: Path, dst: Path, clip: float = 90.0, dump_csv: Path | None = None) -> dict:
    frames, fps = read_frames(src)
    xs = median_filter(person_x(frames))
    anchor = float(np.median(xs))
    shifts = np.clip(anchor - xs, -clip, clip)
    # 亚像素平移 + replicate 边界：不放大、不缩放、不旋转
    out = []
    for f, dx in zip(frames, shifts):
        m = np.array([[1.0, 0.0, dx], [0.0, 1.0, 0.0]], np.float32)
        out.append(cv2.warpAffine(f, m, (f.shape[1], f.shape[0]),
                                  flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
    write_frames(dst, out, fps)
    info = {"src": src.name, "frames": len(frames), "fps": round(fps, 3),
            "anchor_x": round(anchor, 1),
            "swing_before": round(float(xs.max() - xs.min()), 1),
            # 平移后轨迹 = x + shift（不能拿输出视频重新检测：replicate 边界会
            # 被当成前景，2026-11-09 实测把 47px 误读成 102px）
            "swing_after": round(float((xs + shifts).max() - (xs + shifts).min()), 2),
            "max_shift": round(float(np.abs(shifts).max()), 1),
            "out": str(dst)}
    if dump_csv:
        dump_csv.write_text("frame,x,x_smooth,shift\n" + "\n".join(
            f"{i},{xs[i]:.1f},{xs[i]:.1f},{shifts[i]:+.1f}" for i in range(len(xs))),
            encoding="utf-8")
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--out", default="", help="单文件输出路径（只处理一个视频时用）")
    ap.add_argument("--out-dir", default="", help="多文件：输出目录，文件名保持不变")
    ap.add_argument("--clip", type=float, default=90.0, help="单帧最大平移像素（防误检）")
    ap.add_argument("--csv", default="", help="轨迹 CSV 落盘（调试用）")
    args = ap.parse_args()
    if args.out and len(args.videos) != 1:
        sys.exit("--out 只能配单个视频；多个请用 --out-dir")
    for v in args.videos:
        src = Path(v)
        dst = Path(args.out) if args.out else Path(args.out_dir or "assets/drivers_stable") / src.name
        info = stabilize(src, dst, clip=args.clip,
                         dump_csv=Path(args.csv) if args.csv else None)
        print(f"{info['src']}: {info['frames']}帧 {info['fps']}fps | "
              f"横向晃动 {info['swing_before']}px → {info['swing_after']}px "
              f"(锚点x={info['anchor_x']} 最大平移{info['max_shift']}px) → {info['out']}",
              flush=True)


if __name__ == "__main__":
    main()
