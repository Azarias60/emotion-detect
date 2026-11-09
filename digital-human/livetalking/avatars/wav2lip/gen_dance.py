#!/usr/bin/env python3
"""把 dance/ 目录的舞蹈视频并入现有形象的段表，作为 dance 动作段。

和 genavatar_stream.py 的区别：那个是从零生成一个新形象；这个是把
视频**追加**到已有形象（full_imgs / face_imgs / coords.pkl / ranges.json），
让状态机多出一个可播放的 dance 段，播完走 dance_to_idle 过渡回到待机。

用法（在项目根目录）：
    python avatars/wav2lip/gen_dance.py --avatar yy --video dance/v_00001_.mp4

段表里已有 dance 段时会拒绝执行（换视频请加 --replace，会删掉旧舞蹈帧
重建，形象的其他段不受影响）。
"""

import argparse
import json
import os
import pickle
import sys
from pathlib import Path

import cv2
import numpy as np

# 直接以脚本路径运行时把项目根补进 sys.path（与 genavatar_stream 同款问题）
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

parser = argparse.ArgumentParser()
parser.add_argument("--avatar", required=True, help="并入的人物 id，如 yy")
parser.add_argument("--video", required=True, help="舞蹈视频路径")
parser.add_argument("--img_size", default=256, type=int)
parser.add_argument("--pads", nargs="+", type=int, default=[0, 10, 0, 0])
parser.add_argument("--face_det_batch_size", type=int, default=8)
parser.add_argument("--replace", action="store_true",
                    help="段表已有 dance 段时删旧重建")
args = parser.parse_args()

avatar_path = _ROOT / "data" / "avatars" / args.avatar
full_imgs_path = avatar_path / "full_imgs"
face_imgs_path = avatar_path / "face_imgs"
coords_path = avatar_path / "coords.pkl"
ranges_path = avatar_path / "ranges.json"

for required in (full_imgs_path, face_imgs_path, coords_path, ranges_path):
    if not required.exists():
        raise SystemExit(f"人物素材不完整，缺 {required}")

ranges = json.loads(ranges_path.read_text(encoding="utf-8"))
base_total = int(ranges.get("_total", 0))
if "dance" in ranges and not args.replace:
    raise SystemExit(
        f"段表里已有 dance 段 {ranges['dance']}；换视频请加 --replace 重建"
    )
if "dance" in ranges and args.replace:
    # 删掉旧舞蹈帧，回退到加舞前的帧数。只有"舞蹈段在时间线末尾"
    # （本脚本的追加方式）才能这样回退，中间插过其他段就拒绝。
    old_lo, old_hi = ranges["dance"]["range"]
    if base_total != old_hi + 1:
        raise SystemExit(
            f"dance 段 [{old_lo},{old_hi}] 不在时间线末尾（_total={base_total}），"
            "无法安全回退，请手工处理段表"
        )
    old_len = old_hi - old_lo + 1
    for idx in range(old_lo, min(old_hi + 1, base_total)):
        for folder in (full_imgs_path, face_imgs_path):
            target = folder / f"{idx:08d}.png"
            if target.exists():
                target.unlink()
    with open(coords_path, "rb") as f:
        coords = pickle.load(f)
    del coords[old_lo:]
    with open(coords_path, "wb") as f:
        pickle.dump(coords, f)
    keep = {
        name: value
        for name, value in ranges.items()
        if name not in ("dance", "dance_to_idle")
    }
    keep["_total"] = base_total - old_len
    ranges = keep
    ranges_path.write_text(
        json.dumps(ranges, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base_total = ranges["_total"]
    print(f"已移除旧 dance 段，当前 {base_total} 帧")

# ── 1. 抽帧：cover-crop 到与形象帧一致的大小 ──────────────────────────
ref = cv2.imread(str(next(full_imgs_path.glob("*.png"))))
if ref is None:
    raise SystemExit("full_imgs 里没有参照帧")
target_h, target_w = ref.shape[:2]

video_path = Path(args.video)
if not video_path.is_absolute():
    video_path = _ROOT / video_path
if not video_path.is_file():
    raise SystemExit(f"找不到舞蹈视频 {video_path}")
cap = cv2.VideoCapture(str(video_path))
frames = []
while True:
    ok, frame = cap.read()
    if not ok:
        break
    h, w = frame.shape[:2]
    scale = max(target_w / w, target_h / h)  # cover：先放大到铺满，再居中裁
    resized = cv2.resize(
        frame, (int(round(w * scale)), int(round(h * scale))),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )
    rh, rw = resized.shape[:2]
    x0 = (rw - target_w) // 2
    y0 = (rh - target_h) // 2
    frames.append(resized[y0 : y0 + target_h, x0 : x0 + target_w])
cap.release()
if not frames:
    raise SystemExit("视频里抽不到帧")
print(f"抽到 {len(frames)} 帧，缩放到 {target_w}x{target_h}")

start = base_total
end = base_total + len(frames) - 1
for offset, frame in enumerate(frames):
    cv2.imwrite(str(full_imgs_path / f"{start + offset:08d}.png"), frame)

# ── 2. 人脸检测：face_imgs 256x256 + coords（说话时口型贴回要用） ────────
import torch
from avatars.wav2lip import face_detection

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"人脸检测 device={device}")
detector = face_detection.FaceAlignment(
    face_detection.LandmarksType._2D, flip_input=False, device=device
)

pady1, pady2, padx1, padx2 = args.pads
fd_batch = args.face_det_batch_size
predictions = []
while True:
    try:
        for i in range(0, len(frames), fd_batch):
            chunk = np.array(frames[i : i + fd_batch])
            predictions.extend(detector.get_detections_for_batch(chunk))
        break
    except RuntimeError:
        if fd_batch == 1:
            raise RuntimeError("OOM even with batch_size=1")
        fd_batch //= 2
        predictions = []
        print(f"  OOM recovery: fd_batch={fd_batch}")
del detector

boxes = []
last_valid = None
for rect, image in zip(predictions, frames):
    if rect is None:
        if last_valid is not None:
            boxes.append(list(last_valid))
        else:
            # 跳舞时脸可能被手臂挡住：整帧兜底（口型会贴在整帧上，
            # 但舞蹈段静音播放，不走口型路径）
            boxes.append([0, 0, image.shape[1], image.shape[0]])
        continue
    y1 = max(0, int(rect[1]) - pady1)
    y2 = min(image.shape[0], int(rect[3]) + pady2)
    x1 = max(0, int(rect[0]) - padx1)
    x2 = min(image.shape[1], int(rect[2]) + padx2)
    last_valid = [x1, y1, x2, y2]
    boxes.append(last_valid)

# 与 genavatar 相同的滑动窗平滑，舞蹈动作大，平滑能抑制框抖动
boxes = np.array(boxes, dtype=float)
for i in range(len(boxes)):
    window = boxes[max(0, i - 2) : min(len(boxes), i + 3)]
    boxes[i] = np.mean(window, axis=0)

with open(coords_path, "rb") as f:
    coords = pickle.load(f)
assert len(coords) == base_total, f"coords 数 {len(coords)} != _total {base_total}"
for offset, (image, (x1, y1, x2, y2)) in enumerate(zip(frames, boxes)):
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    face_crop = image[y1:y2, x1:x2]
    if face_crop.size == 0:
        x1, y1, x2, y2 = 0, 0, image.shape[1], image.shape[0]
        face_crop = image
    resized = cv2.resize(face_crop, (args.img_size, args.img_size))
    cv2.imwrite(str(face_imgs_path / f"{start + offset:08d}.png"), resized)
    coords.append((y1, y2, x1, x2))
with open(coords_path, "wb") as f:
    pickle.dump(coords, f)

# ── 3. 段表：dance 段 + dance_to_idle 过渡（复用 talk_to_idle 作回场） ──
ranges["dance"] = {"range": [start, end], "source": Path(args.video).name}
if "dance_to_idle" not in ranges:
    bridge = ranges.get("talk_to_idle") or ranges.get("idle_to_idle")
    if bridge:
        ranges["dance_to_idle"] = dict(bridge)
ranges["_total"] = end + 1
ranges_path.write_text(
    json.dumps(ranges, ensure_ascii=False, indent=2), encoding="utf-8"
)

print(
    f"完成：dance 段 [{start}, {end}]（{len(frames)} 帧），"
    f"_total {end + 1}，coords {len(coords)} 条，"
    f"回场过渡 {'dance_to_idle ' + str(ranges['dance_to_idle']['range']) if 'dance_to_idle' in ranges else '无（直切回待机）'}"
)
