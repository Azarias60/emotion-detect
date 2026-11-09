# -*- coding: utf-8 -*-
"""把一个「段目录」组装成 LiveTalking 数字人（studio 第 2 道工序）。

输入：段目录（每个 mp4 是一个动作段或过渡，704x1280）
输出：livetalking/data/avatars/<avatar_id>/（full_imgs + face_imgs + coords.pkl + ranges.json）

步骤：
  1. 按「基础段在前、过渡按名排序」拼成一条 704x1280@25fps 视频（fps 统一，
     20fps 段补帧——补帧不改变动作真实速度）；
  2. 调引擎 genavatar_stream.py 做人脸检测 + 256 对齐裁剪（引擎原生，零改动）；
  3. 按各段时长算帧偏移，写 ranges.json（状态机段表，硬规格见 state_machine.load_avatar_plan）。

用法（引擎 venv）：
    livetalking/venv/Scripts/python.exe studio/build_avatar.py \
        --segments livetalking/guizi-character --avatar-id guizi --display-name 鬼子

注意：段名里的 " to " 会规范成 "_to_"（状态机按 _to_ 认过渡）。
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "livetalking"
for p in (str(ENGINE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

FPS = 25
W, H = 704, 1280


def seg_key(stem: str) -> tuple:
    """排序键：基础段（无 ' to '）排前面，之后过渡按名排序。"""
    is_trans = 0 if " to " not in stem else 1
    return (is_trans, stem)


def norm_name(stem: str) -> str:
    """'idle1 to talk' → 'idle1_to_talk'（状态机的过渡命名）。"""
    return stem.replace(" to ", "_to_")


def probe(video: Path):
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()
    return n, fps


def ffmpeg_exe() -> str:
    """优先用 venv 里 imageio-ffmpeg 自带的二进制，其次系统 PATH。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def build_concat(videos, out: Path):
    """concat filter 拼接并统一到 25fps。"""
    ff = ffmpeg_exe()
    inputs = []
    for v in videos:
        inputs += ["-i", str(v)]
    parts = [f"[{i}:v]fps={FPS},settb=AVTB,format=yuv420p[v{i}]" for i in range(len(videos))]
    concat_in = "".join(f"[v{i}]" for i in range(len(videos)))
    parts.append(f"{concat_in}concat=n={len(videos)}:v=1:a=0[vout]")
    cmd = [ff, "-y", *inputs, "-filter_complex", ";".join(parts),
           "-map", "[vout]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)]
    print("拼接 %d 段 → %s" % (len(videos), out))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise SystemExit("ffmpeg 拼接失败")


def clean_avatar(avatar_id: str):
    """重建前清空目标 avatar 目录。

    genavatar_stream 只 makedirs 不清目录——直接覆盖重建时，旧 full_imgs 里
    超出新视频帧数的 PNG 会残留（实测：旧 2881 帧 + 新 1291 帧 → 目录还是
    2881 个文件，段表被按 2881 缩放，时间线变成新旧混合内容）。
    """
    av = ENGINE / "data" / "avatars" / avatar_id
    if not av.exists():
        return
    import shutil
    for sub in ("full_imgs", "face_imgs"):
        d = av / sub
        if d.is_dir():
            shutil.rmtree(d)
    for f in ("coords.pkl", "ranges.json"):
        pth = av / f
        if pth.is_file():
            pth.unlink()
    print(f"已清空旧 avatar 目录: {av}")


def run_genavatar(avatar_id: str, video: Path):
    """调引擎的 genavatar_stream.py 生成 full_imgs/face_imgs/coords.pkl。"""
    cmd = [str(ENGINE / "venv" / "Scripts" / "python.exe"),
           str(ENGINE / "avatars" / "wav2lip" / "genavatar_stream.py"),
           "--avatar_id", avatar_id, "--video_path", str(video),
           "--face_det_batch_size", "8", "--frame_batch_size", "200"]
    print("人脸检测 + 对齐裁剪（引擎 genavatar_stream）…")
    r = subprocess.run(cmd, cwd=str(ENGINE), capture_output=True, text=True)
    tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-1500:]
    if r.returncode != 0:
        print(tail)
        raise SystemExit("genavatar 失败")
    # 打印关键行
    for line in tail.splitlines():
        if any(k in line for k in ("Face detection", "Saving", "saved", "coords", "%|")):
            print(" ", line[:160])


def write_ranges(avatar_id: str, segments, offsets, total):
    """写状态机段表。segments: [(规范名, 帧数)]，offsets 由调用方算好。"""
    plan = {}
    for name, cnt in segments:
        start = offsets[name]
        plan[name] = {"range": [start, start + cnt - 1], "frames": cnt}
    plan["_total"] = total
    plan["_fps"] = FPS
    path = ENGINE / "data" / "avatars" / avatar_id / "ranges.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print("段表已写:", path, "（%d 段 + _total=%d）" % (len(plan) - 2, total))
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--avatar-id", required=True, help="ASCII id（目录名）")
    ap.add_argument("--display-name", default="", help="页面显示名（可中文）")
    ap.add_argument("--keep-video", action="store_true", help="保留拼接视频")
    args = ap.parse_args()

    seg_dir = Path(args.segments)
    videos = sorted(seg_dir.glob("*.mp4"), key=lambda p: seg_key(p.stem))
    if not videos:
        raise SystemExit("段目录里没有 mp4")

    # 1) 拼接（20fps 段补帧到 25，动作真实速度不变）
    work = ROOT / "runs" / "studio" / args.avatar_id
    work.mkdir(parents=True, exist_ok=True)
    concat_video = work / "concat_25fps.mp4"
    build_concat(videos, concat_video)
    total, _ = probe(concat_video)
    print("拼接视频: %d 帧 @%dfps（%.1fs）" % (total, FPS, total / FPS))

    # 2) 各段帧数（按时长折算，末段吸收取整误差）
    counts = {}
    for v in videos:
        n, fps = probe(v)
        counts[norm_name(v.stem)] = max(1, round(n * FPS / fps))
    drift = total - sum(counts.values())
    if drift:
        last = norm_name(videos[-1].stem)
        counts[last] += drift
        print("取整误差 %+d 帧已并入末段 %s" % (drift, last))
    offsets, acc = {}, 0
    for v in videos:
        name = norm_name(v.stem)
        offsets[name] = acc
        acc += counts[name]

    # 3) genavatar（人脸检测 + 对齐裁剪 + coords.pkl）
    clean_avatar(args.avatar_id)
    run_genavatar(args.avatar_id, concat_video)
    imgs = len(list((ENGINE / "data" / "avatars" / args.avatar_id / "full_imgs").glob("*.png")))
    print("full_imgs 实际帧数:", imgs)
    if imgs != total:
        print("!! 帧数与段表预算不一致（%d vs %d），按实际帧数修正段表" % (imgs, total))
        scale = imgs / total
        counts = {k: max(1, round(c * scale)) for k, c in counts.items()}
        drift = imgs - sum(counts.values())
        if drift:
            counts[norm_name(videos[-1].stem)] += drift
        offsets, acc = {}, 0
        for v in videos:
            name = norm_name(v.stem)
            offsets[name] = acc
            acc += counts[name]
        total = imgs

    # 4) 段表
    segments = [(norm_name(v.stem), counts[norm_name(v.stem)]) for v in videos]
    plan = write_ranges(args.avatar_id, segments, offsets, total)

    if not args.keep_video:
        concat_video.unlink(missing_ok=True)

    print("\n基础段:")
    for name, cnt in segments:
        if "_to_" not in name:
            print("  %-10s %4d 帧 (%.1fs)" % (name, cnt, cnt / FPS))
    print("\n完成。avatar_id=%s（页面显示名建议：%s）" % (args.avatar_id, args.display_name or args.avatar_id))
    print("下一步：把它加进 data/presets.json 配对音色，重启服务即可在页面看到。")


if __name__ == "__main__":
    main()
