# -*- coding: utf-8 -*-
"""基础段几何稳定化（studio 第 2.5 道工序，衔接 qa_segments 与 build_avatar）。

**为什么需要**：SCAIL-2 动作迁移会引入模型固有的缩放漂移——驱动视频本身
稳定（人脸宽度极差 3~7%），但生成段的人脸宽度极差达 17~112%（GLM 视觉核验
确认 idle1 头部放大约 1.5 倍、greet 先靠近后远离）。端点是任意中间态帧，
两个端点的尺度/姿势互不相干（idle2 末帧 vs idle3 首帧像素差 33、脸部区 54），
LTX 首尾帧过渡无法在差异这么大的两端之间插值，只能生成"静态折中帧"——
表现为接缝爆跳+中段僵死。3D 卡通库（2026-10）的解法是稳定化后再熔过渡，
20/20 平滑达标（帧差 ≤4px），本工序把同一方法搬到真人段。

**测量源为什么用 insightface 脸盒而不用 OpenCV 模板匹配**（v1 实测教训）：
haarcascade+多尺度模板匹配会系统性低估漂移——idle1 第 40 帧真实脸宽比
首帧大 36%（247 vs 181），模板拟合只给 19%；且真人暗夜景下匹配分只有
0.79~0.87、idle3/greet 有追踪丢失。insightface 脸盒与 GLM 视觉判断一致
（41% vs "约1.5倍"量级），且 qa_segments/genavatar 本来就用它。

**方法**：
  1. 逐帧 insightface 脸盒 → 宽度/中心（脸部 patch 是姿态不变量，不受
     手臂挥动干扰；逐帧噪声 std ~7% 不能照做）；
  2. 鲁棒拟合（离群剔除 + 二次多项式）提取真实漂移轨迹；
  3. **全局统一目标**（全部段脸宽/脸位中位数）——段间接缝无缝的前提；
  4. 以目标脸中心为锚的仿射校正（纯几何、零调色），BORDER_REPLICATE
     填充避免黑边；
  5. 脚底锚定对真人素材不适用：暗背景下 `亮度>45` 会抓到画面底边噪声
     （实测脚底 y 恒为 1279=底边），且真人脚部常被裁切。

**验收**：稳后脸宽全程波动 <5%；各段首末帧脸宽互差 <5%（过渡端点一致性，
3D 库达标线 <2%，真人素材检测框噪声大放宽到 5%）。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/stabilize_segments.py \
        --segments livetalking/guizi-character --out livetalking/guizi-character-stab
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "livetalking"
for p in (str(ENGINE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from studio.qa_segments import load_detector  # noqa: E402

CORR_LO, CORR_HI = 0.55, 1.80     # 校正系数 clip（真人段摆幅大，放宽）
SWING_GATE = 5.0                  # 稳后脸宽波动验收线（%）


def load(p):
    cap = cv2.VideoCapture(str(p))
    fr = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        fr.append(f)
    cap.release()
    return fr


def write(video, frames, fps):
    h, w = frames[0].shape[:2]
    out = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in frames:
        out.write(f)
    out.release()


def smooth_arr(xs, k=5):
    """轨迹平滑：全局中位数剔假框 → 插值 → 中值 → 轻平均。

    顺序是实测教训（idle3）：必须先剔假框再插值。idle3 第 22~27 帧漏检，
    第 28/29 帧是两个 44px 假脸盒——若先把漏检在 171→45 之间线性插值，
    会拉出 154→68 的垃圾值把局部中位数拖低，假框反而逃过局部剔除，
    整条轨迹被污染成 63→175 的斜坡（稳后中段极差 65%，越修越糟）。
    全局中位数剔假框不依赖邻域，不受漏检段影响。
    """
    v = np.array([x if x is not None else np.nan for x in xs], dtype=np.float64)
    n = len(v)
    valid = v[~np.isnan(v)]
    if len(valid) == 0:
        return v
    gmed = float(np.median(valid))
    v[np.abs(v - gmed) > 0.35 * gmed] = np.nan
    idx = np.arange(n, dtype=np.float64)
    mask = ~np.isnan(v)
    if mask.sum() >= 2:
        v = np.interp(idx, idx[mask], v[mask])
    elif mask.any():
        v = np.full(n, float(v[mask][0]))
    else:
        return v
    if n >= 5:
        ext = np.pad(v, 2, mode="edge")
        v = np.array([np.median(ext[i:i + 5]) for i in range(n)])
    if n >= 3:
        ext = np.pad(v, 1, mode="edge")
        v = np.convolve(ext, np.ones(3) / 3, mode="valid")[:n]
    return v


def measure(frames, det):
    """逐帧脸盒 → (宽度, 中心x, 中心y)，漏检帧 None。"""
    ws, cxs, cys = [], [], []
    for f in frames:
        r = det.get_detections_for_batch(np.array([f]))
        if r and r[0] is not None:
            x1, y1, x2, y2 = r[0]
            ws.append(float(x2 - x1))
            cxs.append(float((x1 + x2) / 2))
            cys.append(float((y1 + y2) / 2))
        else:
            ws.append(None)
            cxs.append(None)
            cys.append(None)
    return ws, cxs, cys


def stabilize(frames, w_fit, cx_fit, cy_fit, t_w, t_cx, t_cy):
    """以目标脸中心为锚的仿射校正：每帧脸宽→t_w、脸中心→(t_cx,t_cy)。"""
    h, w = frames[0].shape[:2]
    # 注意：入参 w_fit/cx_fit/cy_fit 已经过 smooth_arr，这里不能再平滑一次——
    # 双重平滑（中值k5+均值k3 各两遍）会把 idle3 抱胸的凹陷/隆起再次抹平，
    # 实测稳后中段极差从 41% 变成 47%（越修越糟因为校正量被抹掉了）。
    s_traj = np.clip(t_w / np.clip(w_fit, 20.0, 2000.0), CORR_LO, CORR_HI)
    dx_traj = np.clip(t_cx - cx_fit, -80, 80)
    dy_traj = np.clip(t_cy - cy_fit, -80, 80)
    out = []
    for i, f in enumerate(frames):
        s = float(np.clip(s_traj[i], CORR_LO, CORR_HI))
        dx = float(np.clip(dx_traj[i], -80, 80))
        dy = float(np.clip(dy_traj[i], -80, 80))
        # 脸中心 (cx,cy) → (t_cx,t_cy)，尺度 s：p' = t_c + s*(p - c)
        M = np.float32([
            [s, 0, t_cx - s * float(cx_fit[i])],
            [0, s, t_cy - s * float(cy_fit[i])],
        ])
        out.append(cv2.warpAffine(f, M, (w, h), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE))
    return out


def swing(a):
    a = np.asarray(a, dtype=float)
    return 100 * (np.nanmax(a) - np.nanmin(a)) / np.nanmean(a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--apply", action="store_true", help="覆盖写回段目录（默认写 --out）")
    ap.add_argument("--passes", type=int, default=2, help="迭代轮数（默认 2）")
    args = ap.parse_args()
    seg_dir = Path(args.segments)
    if args.apply:
        out_dir = seg_dir
    else:
        if not args.out:
            raise SystemExit("需要 --out（或用 --apply 直接覆盖段目录）")
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)

    det = load_detector()
    names = [p.stem for p in sorted(seg_dir.glob("*.mp4")) if " to " not in p.stem]
    print(f"基础段 {len(names)} 个: {names}")

    # 1) 载入全部段
    frames_all = {}
    for name in names:
        frames_all[name] = load(seg_dir / f"{name}.mp4")
    for name in names:
        ws, _, _ = measure(frames_all[name], det)
        miss = sum(1 for v in ws if v is None)
        print(f"  {name:6s}: {len(frames_all[name])}帧 漏检{miss}  脸宽原始极差 {swing(ws):.1f}%")

    # 2) 多轮迭代：每轮重新测量上一轮产出，修正残余（首轮的平滑滞后/
    #    检测偏差会留下 ~10% 残差，二轮通常压到 <2%）
    passes = args.passes
    current = {name: frames_all[name] for name in names}
    for p in range(passes):
        meas = {}
        for name in names:
            ws, cxs, cys = measure(current[name], det)
            meas[name] = (smooth_arr(ws), smooth_arr(cxs), smooth_arr(cys))
        t_w = float(np.median(np.concatenate([meas[n][0] for n in names])))
        t_cx = float(np.median(np.concatenate([meas[n][1] for n in names])))
        t_cy = float(np.median(np.concatenate([meas[n][2] for n in names])))
        print(f"\n第 {p + 1} 轮目标: 脸宽={t_w:.1f}px 脸中心=({t_cx:.1f},{t_cy:.1f})")
        nxt = {}
        for name in names:
            wf, cxf, cyf = meas[name]
            nxt[name] = stabilize(current[name], wf, cxf, cyf, t_w, t_cx, t_cy)
        current = nxt

    # 3) 最终复测 + 落盘
    print("\n最终复测:")
    ends = []
    for name in names:
        fr = frames_all[name]
        st = current[name]
        ws2, _, _ = measure(st, det)
        wf2 = smooth_arr(ws2)
        g = lambda f: cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        di0 = np.mean([np.abs(g(fr[i]) - g(fr[i + 1])).mean() for i in range(len(fr) - 1)])
        di1 = np.mean([np.abs(g(st[i]) - g(st[i + 1])).mean() for i in range(len(st) - 1)])
        cap = cv2.VideoCapture(str(seg_dir / f"{name}.mp4"))
        fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
        cap.release()
        write(out_dir / f"{name}.mp4", st, fps)
        v = wf2[~np.isnan(wf2)]
        ends += [v[0], v[-1]]
        flag = "" if swing(wf2) < SWING_GATE else " ⚠超门"
        print(f"  {name:6s}: 稳后脸宽极差 {swing(wf2):.1f}%（首 {v[0]:.0f} 末 {v[-1]:.0f}px）"
              f"  帧差比 {di1 / max(di0, 1e-6):.2f}{flag}")

    print(f"\n端点一致性: 各段首末脸宽全体极差 {swing(ends):.1f}%"
          f"（门 {SWING_GATE}%）")
    print(f"输出: {out_dir}")


if __name__ == "__main__":
    main()
