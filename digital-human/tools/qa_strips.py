# -*- coding: utf-8 -*-
"""QA 拼图（GLM-5.3-Flash 视觉验收用）：接缝上下文 / 过渡表情特写 / 全段密抽 / 接缝人脸放大。

用法（项目根目录）：
  python tools/qa_strips.py --idle X.mp4 --trans Y.mp4 --greet Z.mp4 --concat W.mp4 [--out 目录]
  # 四段默认取交付目录里 <run>_1/2/3 与 完整衔接_<run>.mp4
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from comfy import api


def frames_of(p):
    return api.frames_of(p, min_frames=5, retries=3, delay=2)


def parse_args():
    ap = argparse.ArgumentParser(description="QA 拼图生成")
    ap.add_argument("--idle", required=True)
    ap.add_argument("--trans", required=True)
    ap.add_argument("--greet", required=True)
    ap.add_argument("--concat", required=True)
    ap.add_argument("--out", default=None, help="拼图输出目录（默认与 concat 同目录）")
    return ap.parse_args()


def main():
    args = parse_args()
    out = args.out or os.path.dirname(args.concat)
    os.makedirs(out, exist_ok=True)

    fi, ft, fg, fc = (frames_of(args.idle), frames_of(args.trans),
                      frames_of(args.greet), frames_of(args.concat))
    n_i, n_t = len(fi), len(ft)
    print("帧数: idle1", n_i, "过渡", n_t, "greet", len(fg), "拼接", len(fc))

    def imw(name, img):
        api.imwrite_u(os.path.join(out, name), img)

    # 1) 接缝上下文：每个接缝前后各 4 帧，全宽
    def seam_ctx(center, tag, name):
        idx = list(range(max(0, center - 4), min(len(fc), center + 5)))
        row = np.hstack([cv2.resize(fc[i], (210, 382)) for i in idx])
        for j, i in enumerate(idx):
            cv2.putText(row, f"f{i}", (j * 210 + 6, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(row, tag, (6, row.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        imw(name, row)

    seam_ctx(n_i, "SEAM A: idle1 -> transition (concat frames)", "QA_seamA_context.png")
    seam_ctx(n_i + n_t, "SEAM B: transition -> greet (concat frames)", "QA_seamB_context.png")

    # 2) 过渡表情特写：人脸区域放大，取 10 帧
    # 自带 cascade XML：部分 opencv 发行版（如 5.0）不带 cv2.data 数据文件
    casc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                             "haarcascade_frontalface_default.xml")
    if not os.path.exists(casc_path):
        casc_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    casc = cv2.CascadeClassifier(casc_path)
    probe = ft[len(ft) // 2]
    faces = casc.detectMultiScale(cv2.cvtColor(probe, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(60, 60))
    if len(faces):
        fx, fy, fw, fh = max(faces, key=lambda r: r[2] * r[3])
        print("检测到人脸:", fx, fy, fw, fh)
        cx, cy = fx + fw // 2, fy + fh // 2
        half = int(max(fw, fh) * 1.15)
        x0, y0 = max(0, cx - half), max(0, cy - half)
        x1, y1 = min(probe.shape[1], cx + half), min(probe.shape[0], cy + half)
    else:
        print("未检测到人脸，用上中区域兜底")
        x0, y0 = int(probe.shape[1] * 0.22), int(probe.shape[0] * 0.06)
        x1, y1 = int(probe.shape[1] * 0.78), int(probe.shape[0] * 0.46)

    def face_crop(f):
        return cv2.resize(f[y0:y1, x0:x1], (300, 300))

    n = len(ft)
    idx = [int(i * (n - 1) / 9) for i in range(10)]
    row = np.hstack([face_crop(ft[i]) for i in idx])
    for j, i in enumerate(idx):
        cv2.putText(row, f"f{i}", (j * 300 + 6, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    cv2.putText(row, "TRANSITION FACE (expression check)", (6, row.shape[0] - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    imw("QA_transition_face.png", row)

    # 3) idle / greet 全段密抽（每 4 帧一格）查崩坏
    for fr, name, fname in [(fi, "idle1 full scan", "QA_idle1_fullscan.png"),
                            (fg, "greet full scan", "QA_greet_fullscan.png")]:
        idx = list(range(0, len(fr), 4))
        rows = []
        for s in range(0, len(idx), 8):
            chunk = idx[s:s + 8]
            tiles = [cv2.resize(fr[i], (176, 320)) for i in chunk]
            while len(tiles) < 8:
                tiles.append(np.zeros((320, 176, 3), np.uint8))
            rows.append(np.hstack(tiles))
        grid = np.vstack(rows)
        cv2.putText(grid, name, (6, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        imw(fname, grid)
        print("密抽帧数:", len(idx), "->", fname)

    # 4) 接缝人脸放大（人物区域 2 倍裁剪）
    def seam_zoom(a, b, tag, name):
        ca, cb = face_crop(a), face_crop(b)
        row = np.hstack([ca, np.zeros((300, 8, 3), np.uint8), cb])
        cv2.putText(row, tag, (6, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        imw(name, row)

    seam_zoom(fi[-1], ft[0], "SEAM A face zoom: idle1 last | trans first", "QA_seamA_facezoom.png")
    seam_zoom(ft[-1], fg[0], "SEAM B face zoom: trans last | greet first", "QA_seamB_facezoom.png")
    print("拼图完成:", sorted(f for f in os.listdir(out) if f.startswith("QA_")))


if __name__ == "__main__":
    main()
