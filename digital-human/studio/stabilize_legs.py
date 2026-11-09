# -*- coding: utf-8 -*-
"""腿部/脚部抖动修复（studio 第 2.6 道工序，接 interpolate_rife 之后）。

**为什么需要**（2026-11-08 e2egreet01 实测）：SCAIL 逐帧重绘人物，驱动素材
又是头到膝取景（脚不入镜），下半身是模型幻觉——热力图（hf 高频残差）显示
能量集中在裤腿轮廓边线与双脚（idle1 hf腿 2.03 vs hf背景 0.17），衣服/裤子
内部是稳的。RIFE 插帧只吸收一部分（原始 20fps 段 hf 1.6~2.5 → 成品 0.3~2.0），
轮廓颤动依然肉眼可见（用户感知"腿部疯狂抖动"）。

**方法：运动门控时间低通**。逐像素统计两种能量：
  hf   = |帧 - 时间高斯MA| 均值   —— 原地高频颤动（轮廓/脚幻觉）
  slow = |MA(t) - MA(t-1)| 均值   —— 慢运动（挥手、重心移动）
权重 w = clip((hf - slow_k*slow - t0) / t1, 0, 1)，把颤动像素向 13 帧时间
高斯均值拉拢。**slow_k 取小（0.15）是实测教训**：13 帧核窗对慢摆
（0.3~0.6Hz，周期 42~83 帧）几乎全通——慢摆本来就滤不掉，不需要门保护；
slow_k=0.7 的首版会让"快抖骑在慢摆上"的裤边/脚轮廓像素逃逸（修后
hf≈slow 残留）。门的真正职责只是挡手势区，而上半身已由纵向坡道
（y<0.58h 全 0 → y>0.70h 全 1）挡住，slow_k 只兜底。

**验收**（test_out 实测）：hf腿 idle1 2.03→0.99、talk2 1.14→0.69、
greet 0.67→0.57；前后对比条上裤脚/鞋轮廓由逐帧乱跳变为稳定；挥臂/手部
不受影响。脚底接触面永不移动（7 个素材均站姿），强平滑无 ghosting 风险。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/stabilize_legs.py \
        --segments runs/make_avatar/<name>/segments --apply
"""
import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np

# 有效核 = [1,4,6,4,1]/16 自卷积 3 次（13 帧窗，半径 6）。2 遍版（9 帧）对
# 2~3Hz 中频晃压制不足（残留 1.76 vs 1.57）；鬼影实测与 2 遍相当（裤边软边
# 源里就有），压制优先。
_K1 = np.array([1, 4, 6, 4, 1], np.float64) / 16.0
KER = _K1.copy()
for _ in range(2):
    KER = np.convolve(KER, _K1)
RADIUS = len(KER) // 2

# 统计用 2 遍 k5（与验收口径一致）
_KS = np.array([1, 4, 6, 4, 1], np.float32) / 16.0


def read_frames(path):
    cap = cv2.VideoCapture(str(path))
    fr = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        fr.append(f)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()
    return fr, fps


def write_frames_atomic(path, frames, fps):
    tmp = path.with_suffix(".tmp.mp4")
    h, w = frames[0].shape[:2]
    out = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in frames:
        out.write(f)
    out.release()
    os.replace(tmp, path)


def t_gauss_gray(x):
    """灰度栈 (T,H,W) 时间高斯 2 遍 k5（hf/slow 测量口径）。"""
    for _ in range(2):
        pad = np.pad(x, ((2, 2), (0, 0), (0, 0)), mode="edge")
        x = sum(pad[i:i + len(x)] * _KS[i] for i in range(5))
    return x


def v_ramp(h, w, y0=0.58, y1=0.70):
    """纵向渐入：y<y0 全 0，y>y1 全 1，中间 smoothstep。"""
    yy = np.arange(h, dtype=np.float32) / h
    t = np.clip((yy - y0) / (y1 - y0), 0, 1)
    r = (t * t * (3 - 2 * t))[:, None]
    return np.repeat(r, w, axis=1).astype(np.float32)


def hf_of(gray):
    ma = t_gauss_gray(gray)
    return float(np.abs(gray - ma).mean())


def process(path, out_path, strength, t0, t1, slow_k=0.15):
    frames, fps = read_frames(path)
    n = len(frames)
    if n < 2 * RADIUS + 2:
        return None
    h, w = frames[0].shape[:2]

    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                     for f in frames]).astype(np.float32)
    ma = t_gauss_gray(gray)
    hf_map = np.abs(gray - ma).mean(axis=0)
    slow_map = np.abs(np.diff(ma, axis=0)).mean(axis=0)
    wgt = np.clip((hf_map - slow_k * slow_map - t0) / t1, 0, 1).astype(np.float32)
    wgt = cv2.GaussianBlur(wgt, (0, 0), 6)
    wgt = np.minimum(wgt * v_ramp(h, w), 1.0) * strength
    hf_before = hf_of(gray)

    # 颜色时间高斯：中心帧 + 前后各 RADIUS 帧（越界夹紧）加权
    ext = [frames[min(max(i, 0), n - 1)] for i in range(-RADIUS, n + RADIUS)]
    out_frames = []
    for t in range(n):
        acc = np.zeros_like(frames[0], np.float32)
        for k in range(len(KER)):
            acc += ext[t + k].astype(np.float32) * KER[k]
        f32 = frames[t].astype(np.float32)
        fixed = f32 + wgt[..., None] * (acc - f32)
        out_frames.append(np.clip(fixed, 0, 255).astype(np.uint8))

    hf_after = hf_of(np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                               for f in out_frames]).astype(np.float32))
    write_frames_atomic(out_path, out_frames, fps)
    return dict(n=n, gated=float((wgt > 0.25).mean()), hf0=hf_before,
                hf1=hf_after, fps=fps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True, help="段目录")
    ap.add_argument("--apply", action="store_true", help="覆盖写回（默认必须给 --out）")
    ap.add_argument("--out", default="", help="输出目录（不覆盖）")
    ap.add_argument("--only", choices=["base", "transitions", "all"], default="all")
    ap.add_argument("--strength", type=float, default=0.9)
    ap.add_argument("--t0", type=float, default=0.6)
    ap.add_argument("--t1", type=float, default=2.0)
    ap.add_argument("--slow-k", type=float, default=0.15,
                    help="慢运动保护系数：13 帧时间均值本身放过慢摆（0.3~0.6Hz "
                         "周期远大于核窗），此系数只需挡住手势区（纵向坡道已挡"
                         "上半身），实测 0.7 会让'快抖骑在慢摆上'的轮廓像素逃逸")
    args = ap.parse_args()
    seg_dir = Path(args.segments)
    if args.apply:
        out_dir = seg_dir
    elif args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        raise SystemExit("需要 --apply 或 --out")

    vids = [p for p in sorted(seg_dir.glob("*.mp4")) if p.suffix == ".mp4"]
    if args.only == "base":
        vids = [p for p in vids if " to " not in p.stem]
    elif args.only == "transitions":
        vids = [p for p in vids if " to " in p.stem]
    if not vids:
        raise SystemExit(f"{seg_dir} 没有匹配的 mp4")
    print(f"腿部稳定化：{len(vids)} 个文件（{args.only}），"
          f"strength={args.strength} t0={args.t0} t1={args.t1} slow_k={args.slow_k}")
    for p in vids:
        r = process(p, out_dir / p.name, args.strength, args.t0, args.t1,
                    args.slow_k)
        if r is None:
            print(f"  {p.name}: 跳过（帧数不足）")
            continue
        print(f"  {p.name}: {r['n']}帧 门控{100 * r['gated']:.1f}% "
              f"hf {r['hf0']:.2f}→{r['hf1']:.2f}")


if __name__ == "__main__":
    main()
