# -*- coding: utf-8 -*-
"""运动指标分析：帧差三段均值 / 抖动指数 / 单帧尖峰 / 拼接点接缝差。

用法（项目根目录）：
  python tools/analyze_motion.py 视频1.mp4 视频2.mp4 ...
  python tools/analyze_motion.py 拼接.mp4 --seam-at 61 122   # 标出拼接边界帧号
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comfy import api


def analyze(path, seams=()):
    n, fps, w, h = api.video_info(path)
    fr = api.frames_of(path, min_frames=5, retries=3, delay=2)
    d = api.frame_diffs(fr)
    ji = api.jitter_index(fr)
    thl = len(d) // 3
    print(f"\n== {os.path.basename(path)}")
    print(f"  {len(fr)}帧 {len(fr)/max(fps,1e-6):.2f}s @{fps:.0f}fps {w}x{h}")
    print(f"  帧差三段={sum(d[:thl])/max(thl,1):.2f}/{sum(d[thl:2*thl])/max(thl,1):.2f}/"
          f"{sum(d[2*thl:])/max(len(d)-2*thl,1):.2f} 尖峰={max(d):.2f} 抖动指数={ji:.3f}")
    for s in seams:
        if 0 < s < len(d):
            print(f"  接缝@{s}: 前帧差={d[s-1]:.2f} 后帧差={d[s]:.2f}")


def parse_args():
    ap = argparse.ArgumentParser(description="视频运动指标分析")
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--seam-at", type=int, nargs="*", default=(), help="拼接边界帧号")
    return ap.parse_args()


if __name__ == "__main__":
    a = parse_args()
    for v in a.videos:
        analyze(v, a.seam_at)
