# -*- coding: utf-8 -*-
"""动作段质量门：照片→数字人生产线（studio）的第 1 道关。

对一个「段目录」（每个 mp4 是一个动作段）逐段体检，输出通过/失败 + JSON 摘要。
四道门（全部来自段库时代的实测教训）：
  1. 人脸可检性——insightface（引擎 vendor 版）检出率，genavatar 的前提；
  2. 嘴静——按**真实脸盒**算嘴区/额区逐帧差异比（≤2.5 判静；在说的段实测 10+）；
  3. 背景干净——四角亮度标准差小（无复杂环境/水印）；
  4. 漂移——人脸中心水平位移（|dx| 过大说明人物在画面里滑动）。

用法（用引擎的 venv 跑，脚本自己会把引擎根加进 sys.path）：
    livetalking/venv/Scripts/python.exe studio/qa_segments.py \
        --segments livetalking/guizi-character --out runs/qa_guizi
"""
import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]          # digital-human/
ENGINE = ROOT / "livetalking"                        # 实时引擎根
for p in (str(ENGINE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

MOUTH_RATIO_GATE = 2.8      # 对齐脸裁剪后嘴/额运动比 p90 上限（2026-11-05 重标定）
MOUTH_MAX_GATE = 5.5        # 单帧嘴/额运动比上限（对齐后；旧口径 8.0 已不适用）
MOUTH_DARK_GATE = 0.54      # 嘴区暗腔下限：p10(嘴)/median(颊)，张嘴会掉到 ~0.49
FACE_HIT_GATE = 0.98        # 人脸检出率下限
BG_STD_GATE = 26            # 四角背景亮度标准差上限（0~255）
DRIFT_GATE = 40             # 人脸中心水平位移上限（px，704 宽画面；全身段会自然摆动）


def load_detector():
    from avatars.wav2lip import face_detection
    device = "cuda" if cv2.cuda.getCudaEnabledDeviceCount() > 0 else "cpu"
    try:
        import torch
        if torch.cuda.is_available():
            device = "cuda"
    except Exception:
        pass
    return face_detection.FaceAlignment(
        face_detection.LandmarksType._2D, flip_input=False, device=device
    )


def sample_frames(video, count=24):
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    idxs = np.linspace(0, max(n - 1, 0), min(count, max(n, 1))).astype(int)
    out = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if ok:
            out.append(f)
    cap.release()
    return out


def face_crop(frame, box, size=256):
    """按检测框外扩裁脸并归一化：消除头部位移/缩放对嘴部度量的干扰。

    2026-11-05 重标定：原口径直接在整帧固定区域比嘴/额帧差，头部自然摆动
    会让 p90 虚高到 3~4（把闭嘴段误判成说话）。先按每帧自己的脸盒对齐裁切，
    再看嘴/额运动比，闭嘴段 p90 1.9~2.5、张嘴旧素材 max 4.9+暗腔比 0.49。
    """
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    x1 = int(x1 - bw * 0.15)
    x2 = int(x2 + bw * 0.15)
    y1 = int(y1 - bh * 0.20)
    y2 = int(y2 + bh * 0.10)
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 24 or y2 - y1 < 24:
        return None
    return cv2.resize(frame[y1:y2, x1:x2], (size, size))


def gate_segment(video, detector):
    frames = sample_frames(video)
    if len(frames) < 3:
        return {"error": "帧数不足"}
    h, w = frames[0].shape[:2]

    # ① 人脸检出（保留逐帧框，用于对齐裁切；漏检帧记 None）
    boxes = []
    for f in frames:
        rects = detector.get_detections_for_batch(np.array([f]))
        r = rects[0] if rects else None
        boxes.append(tuple(float(v) for v in r) if r is not None else None)
    valid = [b for b in boxes if b is not None]
    hit_rate = len(valid) / len(frames)
    if not valid:
        return {"face_hit": 0.0, "error": "一帧都没检出人脸"}
    bx = float(np.mean([b[0] for b in valid]))
    by = float(np.mean([b[1] for b in valid]))
    bw = float(np.mean([b[2] - b[0] for b in valid]))
    bh = float(np.mean([b[3] - b[1] for b in valid]))

    # ② 嘴静：逐帧对齐脸裁剪 → 嘴/额运动比（p90 + 单帧 max）
    ratios = []
    for i in range(1, len(frames)):
        if boxes[i - 1] is None or boxes[i] is None:
            continue
        a = face_crop(frames[i - 1], boxes[i - 1])
        b = face_crop(frames[i], boxes[i])
        if a is None or b is None:
            continue
        d = np.abs(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
                   - cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32))
        mouth = d[141:210, 77:179].mean()
        fore = d[26:90, 77:179].mean()
        if fore > 0.2:
            ratios.append(float(mouth / fore))
    mouth_p90 = float(np.percentile(ratios, 90)) if ratios else None
    mouth_max = float(max(ratios)) if ratios else None
    mouth_ratio = float(np.median(ratios)) if ratios else None

    # ③ 暗腔：张嘴时嘴内出现暗区，p10(嘴)/median(颊) 会明显下降
    darks = []
    for i, box in enumerate(boxes):
        if box is None:
            continue
        crop = face_crop(frames[i], box)
        if crop is None:
            continue
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
        cheek = float(np.median(g[107:143, 38:77]))
        if cheek > 1:
            darks.append(float(np.percentile(g[148:210, 82:174], 10)) / cheek)
    mouth_dark = min(darks) if darks else None

    # ④ 背景：四角 60x60 的亮度标准差
    corners = np.concatenate([
        cv2.cvtColor(frames[0][:60, :60], cv2.COLOR_BGR2GRAY).ravel(),
        cv2.cvtColor(frames[0][:60, -60:], cv2.COLOR_BGR2GRAY).ravel(),
        cv2.cvtColor(frames[0][-60:, :60], cv2.COLOR_BGR2GRAY).ravel(),
        cv2.cvtColor(frames[0][-60:, -60:], cv2.COLOR_BGR2GRAY).ravel(),
    ])
    bg_std = float(np.std(corners))

    # ⑤ 漂移：人脸中心 x 的极差
    drift = float(max(b[0] + (b[2] - b[0]) / 2 for b in valid)
                  - min(b[0] + (b[2] - b[0]) / 2 for b in valid))

    gates = {
        "face_hit": round(hit_rate, 3) >= FACE_HIT_GATE,
        "mouth_silent": (mouth_p90 is not None and mouth_p90 <= MOUTH_RATIO_GATE
                         and (mouth_max or 0) <= MOUTH_MAX_GATE),
        "mouth_closed": mouth_dark is not None and mouth_dark >= MOUTH_DARK_GATE,
    }
    # 背景/漂移对真人是参考项：实拍背景在全息舞台相框里正常，动作段本来就会动
    advisory = {
        "bg_std_high": bg_std > BG_STD_GATE,
        "drift_large": drift > DRIFT_GATE,
    }
    return {
        "face_hit": round(hit_rate, 3),
        "face_box": [round(v, 1) for v in (bx, by, bw, bh)],
        "mouth_ratio": round(mouth_ratio, 2) if mouth_ratio else None,
        "mouth_p10": round(mouth_dark, 2) if mouth_dark else None,
        "bg_std": round(bg_std, 1),
        "drift_px": round(drift, 1),
        "gates": gates,
        "advisory": advisory,
        "pass": all(gates.values()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True, help="段目录（每个 mp4 一个动作段）")
    ap.add_argument("--out", default="", help="JSON 摘要输出路径（可选）")
    args = ap.parse_args()

    seg_dir = Path(args.segments)
    videos = sorted(seg_dir.glob("*.mp4"))
    bases = [v for v in videos if " to " not in v.stem]  # 基础段（不含过渡）
    print(f"段目录: {seg_dir}  共 {len(videos)} 个 mp4（基础段 {len(bases)} + 过渡 {len(videos)-len(bases)}）")
    print("加载人脸检测器（引擎 vendor insightface）…")
    detector = load_detector()

    results = {}
    print("\n%-10s %-9s %-9s %-8s %-8s %s" % ("段", "人脸检出", "嘴/额比", "背景std", "漂移px", "结论"))
    for v in bases:
        r = gate_segment(v, detector)
        results[v.stem] = r
        if "error" in r and "face_hit" not in r:
            print("%-10s %s" % (v.stem, r["error"]))
            continue
        warn = "（参考项: " + ",".join(k for k, on in r["advisory"].items() if on) + "）" if any(r["advisory"].values()) else ""
        verdict = ("通过 ✓" if r["pass"] else "未过 ✗ " + ",".join(k for k, ok in r["gates"].items() if not ok)) + warn
        print("%-10s %-9s %-9s %-8s %-8s %s" % (
            v.stem, r["face_hit"], r["mouth_ratio"], r["bg_std"], r["drift_px"], verdict))

    ok = sum(1 for r in results.values() if r.get("pass"))
    print(f"\n基础段通过 {ok}/{len(results)}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print("摘要已写:", args.out)
    return 0 if ok == len(results) and results else 1


if __name__ == "__main__":
    sys.exit(main())
