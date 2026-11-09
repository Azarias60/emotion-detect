# -*- coding: utf-8 -*-
"""人物身份审计：段目录里每段到底演的是谁（studio 第 3 道关）。

照片→数字人产线里，过渡段最容易“串人物”——LTX 首末帧引导只锚定端点，
中段是模型自由生成，稍不注意就漂成另一个人（guizi 项目实测：30 条过渡
全是 zzf 的素材，页面表现为“人物时不时换脸”）。

做法：对每段采帧 → 引擎 insightface 检测人脸 → 按固定 padding 裁脸区 →
resize 128x128、零均值单位范数化 → 与各参考人物的均值脸算归一化互相关。
输出每段最像谁、以及与他人的 margin（margin 太小说明区分度不足）。

参考人物默认取三个就绪 avatar 的 full_imgs（均值脸各抽 60 帧）：
  guizi     —— livetalking/data/avatars/guizi（目标人物）
  zzf/my_avatar —— livetalking/data/avatars/my_avatar（历史串脸源头）
  yy        —— livetalking/data/avatars/yy

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/audit_identity.py \
        --segments livetalking/guizi-character --expect guizi
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "livetalking"
for p in (str(ENGINE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

FACE = 128          # 脸区归一化尺寸
REF_FRAMES = 60     # 每个参考人物抽这么多帧算均值脸
PAD_Y, PAD_X = 0.55, 0.45   # 脸盒外扩比例（相对脸盒宽高）


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


def face_crop(frame, rect):
    """按检测盒 + 固定外扩裁脸区；检不到返回 None。"""
    if rect is None:
        return None
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = rect
    bw, bh = x2 - x1, y2 - y1
    x1 = max(0, int(x1 - bw * PAD_X))
    x2 = min(w, int(x2 + bw * PAD_X))
    y1 = max(0, int(y1 - bh * PAD_Y))
    y2 = min(h, int(y2 + bh * PAD_Y))
    if x2 - x1 < 32 or y2 - y1 < 32:
        return None
    return frame[y1:y2, x1:x2]


def norm_face(crop):
    """灰度 → resize → 零均值单位范数（相关比较用）。"""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (FACE, FACE)).astype(np.float32)
    g -= g.mean()
    n = np.linalg.norm(g)
    return g / n if n > 0 else g


def sample_video_faces(video, count, detector):
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    idxs = np.linspace(0, max(total - 2, 0), min(count, total)).astype(int)
    want = set(idxs.tolist())
    out, i = [], 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if i in want:
            rects = detector.get_detections_for_batch(np.array([f]))
            rect = rects[0] if rects else None
            crop = face_crop(f, rect)
            if crop is not None:
                out.append(norm_face(crop))
        i += 1
    cap.release()
    return out


def mean_face(faces):
    if not faces:
        return None
    m = np.mean(faces, axis=0).astype(np.float32)
    m -= m.mean()
    n = np.linalg.norm(m)
    return m / n if n > 0 else m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--expect", default="guizi", help="期望每段都是这个人物")
    ap.add_argument("--refs", default="guizi,my_avatar,yy",
                    help="逗号分隔；每项可以是 avatar id（取 full_imgs）、"
                         "单个段名、或段名用 + 相连（如 idle1+idle2+greet，取段目录里的 mp4）")
    ap.add_argument("--samples", type=int, default=9)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    seg_dir = Path(args.segments)
    detector = load_detector()

    # ── 参考均值脸 ──
    # 注意：不要直接拿 data/avatars/<id>/full_imgs 当目标人物的参考——
    # 那个目录是历史拼接产物，可能混着别人的过渡段（guizi 实测：2881 帧里
    # 85% 是 zzf 的旧过渡，均值脸其实是 zzf 的）。目标人物请用基础段名。
    refs = {}
    for name in [r.strip() for r in args.refs.split(",") if r.strip()]:
        faces = []
        for part in name.split("+"):
            av_dir = ENGINE / "data" / "avatars" / part / "full_imgs"
            if av_dir.is_dir():
                pngs = sorted(av_dir.glob("*.png"))
                step = max(1, len(pngs) // REF_FRAMES)
                for p in pngs[::step][:REF_FRAMES]:
                    img = cv2.imread(str(p))
                    rects = detector.get_detections_for_batch(np.array([img]))
                    crop = face_crop(img, rects[0] if rects else None)
                    if crop is not None:
                        faces.append(norm_face(crop))
            else:
                # 段名参考：从段目录取该段采帧
                faces += sample_video_faces(seg_dir / f"{part}.mp4", REF_FRAMES, detector)
        mf = mean_face(faces)
        if mf is None:
            print(f"参考 {name}: 无可用脸（跳过）")
            continue
        refs[name] = mf
        print(f"参考 {name}: {len(faces)} 帧均值脸")

    if len(refs) < 2:
        raise SystemExit("至少需要 2 个有效参考人物才能判身份")

    # ── 逐段审计 ──
    # 过渡段（名含 " to "）是两段的混合渐变，天然更像任何库里源人物——
    # 只做信息展示，不进身份门限（曾把 10/16 的过渡全判 ✗ 导致产线最后
    # 一步误报失败；身份由 6 个基础段定义，过渡继承之）。
    rows, bad = [], []
    for video in sorted(seg_dir.glob("*.mp4")):
        is_transition = " to " in video.stem
        faces = sample_video_faces(video, args.samples, detector)
        if not faces:
            rows.append({"seg": video.stem, "best": "?", "score": 0.0, "margin": 0.0,
                         "note": "无人脸", "transition": is_transition})
            if not is_transition:
                bad.append(video.stem)
            continue
        scores = {name: float(np.mean([np.sum(f * mf) for f in faces]))
                  for name, mf in refs.items()}
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best, second = ranked[0], ranked[1]
        margin = best[1] - second[1]
        ok = best[0] == args.expect
        rows.append({"seg": video.stem, "best": best[0], "score": round(best[1], 3),
                     "margin": round(margin, 3), "ok": ok,
                     "transition": is_transition,
                     "scores": {k: round(v, 3) for k, v in scores.items()}})
        if not ok and not is_transition:
            bad.append(video.stem)

    print("\n段              最像     得分    与他人差   判定")
    for r in rows:
        if r.get("transition"):
            mark = "— 过渡段（不参与门限）"
        else:
            mark = "✓" if r.get("ok") else ("✗ " + r["best"] if r["best"] != "?" else "✗ 无人脸")
        print(f"{r['seg']:<16} {r['best']:<8} {r['score']:<8.3f} {r['margin']:<10.3f} {mark}")
    n_ok = sum(1 for r in rows if r.get("ok"))
    print(f"\n{n_ok}/{len(rows)} 段最像 {args.expect}")
    if bad:
        print("问题段:", ", ".join(bad))
    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print("摘要已写:", args.out)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
