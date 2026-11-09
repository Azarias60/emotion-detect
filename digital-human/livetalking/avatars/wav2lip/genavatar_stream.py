#!/usr/bin/env python3
"""Streaming genavatar — processes face detection in batches to avoid OOM."""
import numpy as np, cv2, os, sys, argparse, pickle

# 以脚本路径直接运行时（`python avatars/wav2lip/genavatar_stream.py`），
# sys.path[0] 是 avatars/wav2lip 而不是项目根，下面那句
# `from avatars.wav2lip import face_detection` 会 ModuleNotFoundError。
# server/assets.start_avatar_generation 正是这么调的 —— 所以"生成人物"
# 按钮以前必定失败（错误只落到 data/drop/_gen/<名字>.log 里，页面上看不到）。
# 这里把项目根补进 sys.path，怎么调用都能跑。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tqdm import tqdm
import torch
from avatars.wav2lip import face_detection

parser = argparse.ArgumentParser()
parser.add_argument('--img_size', default=256, type=int)
parser.add_argument('--avatar_id', default='my_avatar', type=str)
parser.add_argument('--video_path', default='', type=str)
parser.add_argument('--nosmooth', action='store_true')
parser.add_argument('--pads', nargs='+', type=int, default=[0, 10, 0, 0])
parser.add_argument('--face_det_batch_size', type=int, default=8)
parser.add_argument('--frame_batch_size', type=int, default=200,
                    help='How many frames to load at once (lower = less RAM)')
args = parser.parse_args()

device = 'cuda' if torch.cuda.is_available() else 'cpu'
print(f'Device: {device}')

avatar_path = f"./data/avatars/{args.avatar_id}"
full_imgs_path = f"{avatar_path}/full_imgs"
face_imgs_path = f"{avatar_path}/face_imgs"
coords_path = f"{avatar_path}/coords.pkl"
os.makedirs(avatar_path, exist_ok=True)
os.makedirs(full_imgs_path, exist_ok=True)
os.makedirs(face_imgs_path, exist_ok=True)

# Step 1: Extract frames from video (if not already done)
existing = len([f for f in os.listdir(full_imgs_path) if f.endswith('.png')])
cap = cv2.VideoCapture(args.video_path)
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
cap.release()

if existing >= total_frames:
    print(f'full_imgs: {existing} already exist, skipping extraction')
else:
    print(f'Extracting {total_frames} frames from video...')
    cap = cv2.VideoCapture(args.video_path)
    idx = 0
    for idx in tqdm(range(total_frames)):
        ret, frame = cap.read()
        if not ret: break
        cv2.imwrite(f"{full_imgs_path}/{idx:08d}.png", frame)
    cap.release()
    print(f'Extracted {idx+1} frames')

# Step 2: Face detection in batches
import glob
input_img_list = sorted(glob.glob(os.path.join(full_imgs_path, '*.[jpJP][pnPN]*[gG]')))
total = len(input_img_list)
print(f'Face detection on {total} frames (batch_size={args.face_det_batch_size}, frame_batch={args.frame_batch_size})')

detector = face_detection.FaceAlignment(face_detection.LandmarksType._2D,
                                         flip_input=False, device=device)

def get_smoothened_boxes(boxes, T=5):
    for i in range(len(boxes)):
        window = boxes[max(0, i-T//2): min(len(boxes), i+T//2+1)]
        boxes[i] = np.mean(window, axis=0)
    return boxes

all_coords = []
pady1, pady2, padx1, padx2 = args.pads

# Process frames in batches to limit RAM usage
for batch_start in tqdm(range(0, total, args.frame_batch_size)):
    batch_end = min(batch_start + args.frame_batch_size, total)
    batch_paths = input_img_list[batch_start:batch_end]

    # Load this batch of frames
    batch_frames = []
    for p in batch_paths:
        batch_frames.append(cv2.imread(p))

    # Run face detection
    predictions = []
    fd_batch = args.face_det_batch_size
    while True:
        try:
            for i in range(0, len(batch_frames), fd_batch):
                chunk = np.array(batch_frames[i:i+fd_batch])
                predictions.extend(detector.get_detections_for_batch(chunk))
            break
        except RuntimeError:
            if fd_batch == 1:
                raise RuntimeError('OOM even with batch_size=1')
            fd_batch //= 2
            predictions = []
            print(f'  OOM recovery: fd_batch={fd_batch}')

    # Process results
    results = []
    last_valid = None
    for rect, image in zip(predictions, batch_frames):
        if rect is None:
            if last_valid is not None:
                results.append(list(last_valid))
            else:
                results.append([0, 0, image.shape[1], image.shape[0]])
        else:
            y1 = max(0, rect[1] - pady1)
            y2 = min(image.shape[0], rect[3] + pady2)
            x1 = max(0, rect[0] - padx1)
            x2 = min(image.shape[1], rect[2] + padx2)
            last_valid = [x1, y1, x2, y2]
            results.append(last_valid)

    boxes = np.array(results)
    if not args.nosmooth:
        # Smooth within this batch (with overlap from previous batch)
        boxes = get_smoothened_boxes(boxes)

    # Save face_imgs and coords
    for j, (image, (x1, y1, x2, y2)) in enumerate(zip(batch_frames, boxes)):
        global_idx = batch_start + j
        face_crop = image[y1:y2, x1:x2]
        resized = cv2.resize(face_crop, (args.img_size, args.img_size))
        cv2.imwrite(f"{face_imgs_path}/{global_idx:08d}.png", resized)
        all_coords.append((y1, y2, x1, x2))

    # Free memory
    del batch_frames, predictions, results, boxes

del detector

with open(coords_path, 'wb') as f:
    pickle.dump(all_coords, f)

print(f'Done: {len(all_coords)} face_imgs, coords saved')
