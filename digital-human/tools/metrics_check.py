# -*- coding: utf-8 -*-
"""talk→过渡→talk 链条指标门（v1 方案执行链(1)）。

用法一（从视频现算并判定，方案 CLI）：
  python tools/metrics_check.py --talk x.mp4 --trans y.mp4 --concat z.mp4
      [--mouth-roi y0 y1 x0 x1 --torso-roi y0 y1 x0 x1 --out metrics.json]

用法二（读 metrics.json 判定，交付目录内自包含）：
  python metrics_check.py --metrics-json metrics.json

指标定义（与 pipeline/make_talk.py 写出的 metrics.json 一致）：
  framesTalk/framesTrans  段帧数
  talkSpike/talkMean      talk 段相邻帧灰度差序列 max/mean（api.frame_diffs 口径）
  transSpike/transMean    过渡段同上
  seamMotionA/B           拼接跨缝帧差：talk 末→过渡 首 / 过渡 末→talk 首
  flickerDeltaTalk        talk 帧差序列奇偶位均值之差（隔帧闪烁形态）
  talkSecondDiff/transSecondDiff  段内帧差二阶差分 max（辅助：>5 打印 top-3 帧号）
  mouthMotion/bodyMotion  talk 段嘴/躯干 ROI 帧差均值（api.roi_mean_diff 口径）
  talkMouthRatio/transMouthRatio  嘴/躯干比，talk 与 trans 各判
  嘴部辅助判据（只打印不作硬门）：
  mouthDarkPxRange        talk 段嘴 ROI 暗像素(<90)数 min/max（张合振荡幅度）
  mouthDarkCrossings      暗像素序列跨越中位数次数（开合周期数，D 标定 10 次）
  mouthDiffGT8            嘴 ROI 相邻帧差 >8 的帧数（D 标定 20）

门限（v2 方案 thresholds，按本链 LTX 段实测定标——2026-10-14 标定轮实测：
冻结底线=单端锚变体 ratio 0.84，真嘴动=1.69~2.70（F1→F2/F2→F1 微差镜像双锚），
故 minMouthBodyRatio=1.3；v32 段的 1.94 冻嘴上限是 SCAIL 噪声域、不适用本链）：
  maxTalkSpike=12  maxTransSpike=12（本链实测段尖峰 1.85~6.89，跳帧级 19+ 仍拦）
  maxSeamMotion=5（本链实测 2.17/1.91；3~5 人工复核区；>5 走 color_match 预案）
  maxFlickerDelta=8（本链实测奇偶差 D=0.032/T=0.262）
exit 0=全过；exit 1=有不过项（打印每项差多少）。
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

# 在仓库内运行时复用 comfy/api.py（方案要求）；交付目录副本则退化为本地等价实现
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from comfy import api as _api
    frames_of = _api.frames_of
    frame_diffs = _api.frame_diffs
    roi_mean_diff = _api.roi_mean_diff
    _SRC = "comfy.api"
except Exception:  # 独立运行（版本文件夹副本）
    def frames_of(path, min_frames=30, retries=8, delay=4):
        """与 api.frames_of 同口径：NVENC 写延迟重试。"""
        for _ in range(retries):
            cap = cv2.VideoCapture(path)
            fr = []
            while True:
                ok, f = cap.read()
                if not ok:
                    break
                fr.append(f)
            cap.release()
            if len(fr) >= min_frames:
                return fr
            time.sleep(delay)
        return fr

    def _gray(img):
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)

    def frame_diffs(frames):
        g = [_gray(f) for f in frames]
        return [float(np.abs(g[i] - g[i + 1]).mean()) for i in range(len(g) - 1)]

    def roi_mean_diff(frames, y0, y1, x0, x1):
        g = [_gray(f)[y0:y1, x0:x1] for f in frames]
        return float(np.mean([np.abs(g[i] - g[i + 1]).mean() for i in range(len(g) - 1)]))
    _SRC = "本地实现（等价口径）"

DEFAULT_THR = {"maxTalkSpike": 12.0, "maxTransSpike": 12.0, "maxSeamMotion": 5.0,
               "maxFlickerDelta": 8.0, "minMouthBodyRatio": 1.3}
DEFAULT_MOUTH_ROI = [340, 400, 280, 400]   # v2：F 帧实测嘴 x303-367/y358-378（裁剪视觉+暗带交叉）
DEFAULT_TORSO_ROI = [700, 1020, 190, 500]  # make_chain.py:212 口径


def mouth_aux(fr, mouth_roi):
    """嘴部辅助判据：暗px 振荡范围 / 中位跨越次数 / 相邻帧差>8 计数。"""
    y0, y1, x0, x1 = mouth_roi
    g = [_gray_local(f)[y0:y1, x0:x1] for f in fr]
    dark = [int((gi < 90).sum()) for gi in g]
    med = float(np.median(dark))
    md = [float(np.abs(g[i].astype(np.float32) - g[i + 1].astype(np.float32)).mean())
          for i in range(len(g) - 1)]
    return {"mouthDarkPxRange": [min(dark), max(dark)],
            "mouthDarkCrossings": int(sum(1 for i in range(len(dark) - 1)
                                          if (dark[i] - med) * (dark[i + 1] - med) < 0)),
            "mouthDiffGT8": int(sum(1 for v in md if v > 8))}


def _gray_local(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def seg_metrics(fr, mouth_roi, torso_roi):
    d = frame_diffs(fr)
    sec = [abs(d[i + 1] - d[i]) for i in range(len(d) - 1)]
    flick = abs(float(np.mean(d[1::2])) - float(np.mean(d[0::2]))) if len(d) >= 4 else 0.0
    mouth = roi_mean_diff(fr, *mouth_roi)
    torso = roi_mean_diff(fr, *torso_roi)
    return {"frames": len(fr), "spike": max(d) if d else 0.0,
            "mean": float(np.mean(d)) if d else 0.0,
            "secondDiff": max(sec) if sec else 0.0, "secondDiffAll": sec,
            "flicker": flick, "mouth": mouth, "torso": torso,
            "ratio": mouth / max(torso, 1e-6)}


def compute_metrics(talk_mp4, trans_mp4, concat_mp4, mouth_roi, torso_roi):
    ft = frames_of(talk_mp4)
    ftr = frames_of(trans_mp4)
    fc = frames_of(concat_mp4)
    if min(len(ft), len(ftr), len(fc)) < 10:
        raise RuntimeError(f"读帧失败 talk={len(ft)} trans={len(ftr)} concat={len(fc)}")
    mt, mtr = seg_metrics(ft, mouth_roi, torso_roi), seg_metrics(ftr, mouth_roi, torso_roi)
    dc = frame_diffs(fc)
    a, b = len(ft) - 1, len(ft) + len(ftr) - 1
    m = {
        "framesTalk": mt["frames"], "framesTrans": mtr["frames"],
        "talkSpike": mt["spike"], "talkMean": mt["mean"],
        "transSpike": mtr["spike"], "transMean": mtr["mean"],
        "seamMotionA": dc[a], "seamMotionB": dc[b],
        "flickerDeltaTalk": mt["flicker"],
        "mouthMotion": mt["mouth"], "bodyMotion": mt["torso"],
        "mouthRoi": list(mouth_roi), "bodyRoi": list(torso_roi),
        # 附加字段（metrics_check 同样打印/判定）
        "talkSecondDiff": mt["secondDiff"], "transSecondDiff": mtr["secondDiff"],
        "transMouthMotion": mtr["mouth"], "transBodyMotion": mtr["torso"],
        "talkMouthRatio": mt["ratio"], "transMouthRatio": mtr["ratio"],
        "mouthMotionSource": "talk 段",
        "_aux": {"talkSecTop3": sorted(range(len(mt["secondDiffAll"])),
                                       key=lambda i: -mt["secondDiffAll"][i])[:3],
                 "talkSecVals": mt["secondDiffAll"],
                 "transSecTop3": sorted(range(len(mtr["secondDiffAll"])),
                                        key=lambda i: -mtr["secondDiffAll"][i])[:3],
                 "transSecVals": mtr["secondDiffAll"],
                 "talkDiffOdd": float(np.mean(frame_diffs(ft)[1::2])),
                 "talkDiffEven": float(np.mean(frame_diffs(ft)[0::2]))},
    }
    m.update(mouth_aux(ft, mouth_roi))  # 嘴部辅助判据（talk 段）
    return m


def aux_report(m):
    """二阶差分 >5 时的辅助报告：top-3 帧号 + 奇/偶帧差均值（判奇偶交替闪烁）。"""
    aux = m.get("_aux")
    if not aux:
        return
    for tag, key_t, key_v in [("talk", "talkSecTop3", "talkSecVals"),
                              ("trans", "transSecTop3", "transSecVals")]:
        if m.get(tag + "SecondDiff", 0) > 5:
            tops = ", ".join(f"帧{i + 1}->{i + 2}:{aux[key_v][i]:.2f}" for i in aux[key_t])
            print(f"  [辅助] {tag} 二阶差分 max={m[tag + 'SecondDiff']:.2f} >5 "
                  f"top-3: {tops}")
    print(f"  [辅助] talk 帧差 奇位均值={aux['talkDiffOdd']:.2f} "
          f"偶位均值={aux['talkDiffEven']:.2f}（接近=无奇偶交替闪烁）")
    print(f"  [辅助] talk 嘴部: darkPxRange={m.get('mouthDarkPxRange')} "
          f"中位跨越={m.get('mouthDarkCrossings')}（开合周期数, D 标定=10） "
          f"帧差>8 计数={m.get('mouthDiffGT8')}（D 标定=20）——"
          f"振荡范围≥800 且跨越≥1 视为嘴动结构成立")


def judge(m, thr):
    checks = [
        ("talkSpike", m["talkSpike"], "<=", thr["maxTalkSpike"], "maxTalkSpike"),
        ("transSpike", m["transSpike"], "<=", thr["maxTransSpike"], "maxTransSpike"),
        ("seamMotionA", m["seamMotionA"], "<=", thr["maxSeamMotion"], "maxSeamMotion"),
        ("seamMotionB", m["seamMotionB"], "<=", thr["maxSeamMotion"], "maxSeamMotion"),
        ("flickerDeltaTalk", m["flickerDeltaTalk"], "<=", thr["maxFlickerDelta"],
         "maxFlickerDelta"),
        ("talkMouthRatio", m.get("talkMouthRatio", 0.0), ">=", thr["minMouthBodyRatio"],
         "minMouthBodyRatio"),
        ("transMouthRatio", m.get("transMouthRatio", 0.0), ">=", thr["minMouthBodyRatio"],
         "minMouthBodyRatio"),
    ]
    fails = 0
    print(f"指标实现来源: {_SRC}")
    print(f"帧数: talk={m['framesTalk']} trans={m['framesTrans']} "
          f"(拼接应={m['framesTalk'] * 2 + m['framesTrans']})")
    for name, val, op, limit, lname in checks:
        ok = val <= limit if op == "<=" else val >= limit
        margin = (limit - val) if op == "<=" else (val - limit)
        print(f"  {name}={val:.3f} {op} {lname}({limit}) -> "
              f"{'PASS' if ok else 'FAIL'}（余量 {margin:+.3f}）")
        fails += 0 if ok else 1
    for s in ("seamMotionA", "seamMotionB"):
        if 3.0 < m[s] <= thr["maxSeamMotion"]:
            print(f"  [人工复核区] {s}={m[s]:.2f} 在 3.0~{thr['maxSeamMotion']}，"
                  f"看 QA 接缝特写确认")
    aux_report(m)
    return fails


def main():
    ap = argparse.ArgumentParser(description="talk 链指标门（v1）")
    ap.add_argument("--talk")
    ap.add_argument("--trans")
    ap.add_argument("--concat")
    ap.add_argument("--metrics-json")
    ap.add_argument("--mouth-roi", type=int, nargs=4, default=None,
                    metavar=("y0", "y1", "x0", "x1"))
    ap.add_argument("--torso-roi", type=int, nargs=4, default=None,
                    metavar=("y0", "y1", "x0", "x1"))
    ap.add_argument("--out", default=None, help="把现算指标写为 metrics.json")
    ap.add_argument("--thr", type=float, nargs=2, action="append", default=None,
                    metavar=("名", "值"), help="覆盖门限，如 --thr maxSeamMotion 5")
    args = ap.parse_args()

    thr = dict(DEFAULT_THR)
    if args.thr:
        thr.update({k: v for k, v in args.thr})

    if args.metrics_json:
        with open(args.metrics_json, encoding="utf-8") as f:
            m = json.load(f)
        mr = m.get("mouthRoi") or (args.mouth_roi or DEFAULT_MOUTH_ROI)
        br = m.get("bodyRoi") or (args.torso_roi or DEFAULT_TORSO_ROI)
    elif args.talk and args.trans and args.concat:
        mr = args.mouth_roi or DEFAULT_MOUTH_ROI
        br = args.torso_roi or DEFAULT_TORSO_ROI
        m = compute_metrics(args.talk, args.trans, args.concat, mr, br)
        if args.out:
            m2 = {k: v for k, v in m.items() if not k.startswith("_")}
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(m2, f, ensure_ascii=False, indent=2)
            print(f"指标已写出: {args.out}")
    else:
        ap.error("需要 --metrics-json 或 --talk/--trans/--concat 三件套")
        return 2
    print(f"mouthRoi={m.get('mouthRoi', mr)} bodyRoi={m.get('bodyRoi', br)}")
    print(f"talkMean={m['talkMean']:.3f} transMean={m['transMean']:.3f} "
          f"talkSecondDiff={m.get('talkSecondDiff', float('nan')):.3f} "
          f"transSecondDiff={m.get('transSecondDiff', float('nan')):.3f}")
    print(f"mouthMotion={m['mouthMotion']:.3f} bodyMotion={m['bodyMotion']:.3f} "
          f"talkRatio={m.get('talkMouthRatio', float('nan')):.3f} "
          f"transRatio={m.get('transMouthRatio', float('nan')):.3f}")
    fails = judge(m, thr)
    if fails == 0:
        print("PASS: 全部指标门通过")
        return 0
    print(f"FAIL: {fails} 项未过门")
    return 1


if __name__ == "__main__":
    sys.exit(main())
