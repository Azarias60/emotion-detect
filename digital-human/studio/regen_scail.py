# -*- coding: utf-8 -*-
"""SCAIL-2 动作迁移段批量重生成（鬼子段专用，2026-10-21 提示词升级版）。

背景：鬼子现有基础段由 SCAIL-2 生成于 2026-10-08/09，当时的工作流图正负
提示词为空（nodes 3/4 text=""），模型自由发挥导致镜头漂移、奇怪表情、
缩放漂移（脸宽极差 41~112%）。本脚本用升级提示词重生成全部基础段。

防坑（v8 全段误用旧源的教训）：
  - 驱动视频复制进 ComfyUI input 时用唯一名 s{run}_{seg}.mp4，复制后做
    字节校验——同名跳过复制的默认行为曾让整轮生成用错旧源。

用法（项目根 digital-human/，用引擎 venv）：
  python studio/regen_scail.py submit --segments idle1,idle2,idle3,greet,talk \
      --seeds 1,2 --run guizi_s1 [--recipe scail_guizi.json]
  python studio/regen_scail.py collect --run guizi_s1
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "livetalking"
for p in (str(ENGINE), str(ROOT), str(ROOT / "generation")):
    if p not in sys.path:
        sys.path.insert(0, p)

import cv2
import numpy as np

import config
from comfy import api
from graphs import scail

DRIVER_DIR = Path("E:/Comfyui")
POSE_DIR = ROOT / "assets" / "pose"
OUTPUT_WAIT = 8 * 3600

# 动作段 → 提示词键（配方顶层字段）；无对应键的段回退 idle_prompt
_PROMPT_KEY = {
    "greet": "greet_prompt",
    "talk": "talk_prompt", "talk2": "talk_prompt",
    "comfort": "comfort_prompt",
    "encourage": "encourage_prompt",
}


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def face_widths(video: Path, step: int = 5):
    sys.path.insert(0, str(ENGINE))
    from studio.qa_segments import load_detector

    det = load_detector()
    cap = cv2.VideoCapture(str(video))
    ws = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        r = det.get_detections_for_batch(np.array([f]))
        ws.append(float(r[0][2] - r[0][0]) if r and r[0] is not None else np.nan)
    cap.release()
    return np.array(ws, dtype=float)


def swing(ws):
    v = ws[~np.isnan(ws)]
    if len(v) < 3:
        return float("nan")
    return 100 * (v.max() - v.min()) / v.mean()


def load_recipes(name: str) -> dict:
    return json.loads((ROOT / "generation" / "recipes" / name).read_text(encoding="utf-8"))


def cmd_submit(args):
    run_dir = ROOT / "runs" / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    recipe = load_recipes(args.recipe)
    global DRIVER_DIR
    if args.driver_dir:
        DRIVER_DIR = Path(args.driver_dir)
    mapping = {}
    segments = [s.strip() for s in args.segments.split(",") if s.strip()]
    seeds = [int(s) for s in args.seeds.split(",")]

    # 外观描述（视觉 LLM 看上传照片写的英文外观句）：组合进每段提示词，
    # 让 SCAIL 文本侧也锚定照片里的真人（中性指代之外的强化锁）
    appearance = ""
    if args.appearance_file and Path(args.appearance_file).is_file():
        appearance = Path(args.appearance_file).read_text(encoding="utf-8").strip()
    workflow_dir = Path(args.workflow_dir) if args.workflow_dir else None
    if workflow_dir:
        workflow_dir.mkdir(parents=True, exist_ok=True)

    for seg in segments:
        src = DRIVER_DIR / f"{seg}.mp4"
        if not src.is_file():
            src = POSE_DIR / f"{seg}.mp4"
        if not src.is_file():
            raise SystemExit(f"驱动视频不存在：{src}")
        digest = sha1_of(src)
        # 唯一名 + 字节校验：同名文件 ComfyUI 会跳过复制，可能用到旧源
        uname = f"s{args.run}_{seg}.mp4"
        upath = Path(config.COMFY_INPUT) / uname
        need_copy = True
        if upath.is_file():
            need_copy = sha1_of(upath) != digest
        if need_copy:
            import shutil

            shutil.copy2(src, upath)
            assert sha1_of(upath) == digest, f"{uname} 复制后校验失败"
        cap_info = api.video_info(str(src))
        cap = int(cap_info[0])
        # 底快照只分 greet/idle 两种图结构；提示词按动作分段选——talk/comfort/
        # encourage 不能再套 idle 的 "no large or sudden movements"（文本压制
        # 与驱动视频的动作对抗，动作段发僵发怪，2026-11-08 实锤）
        which = "greet" if seg == "greet" else "idle"
        prompt = recipe[_PROMPT_KEY.get(seg, "idle_prompt")]
        if appearance:
            # 外观句替换中性主语短语（配方开头固定为该短语，其余为动作/稳定性描述）
            prompt = prompt.replace(
                "The person shown in the reference image", appearance, 1)
            prompt = prompt.replace("they appear in the reference image",
                                    "they appear in the reference photo", 1)
        for seed in seeds:
            ref = None
            if args.ref_image:
                ref = api.ensure_input_file(args.ref_image)
            graph = scail.build(
                which, pose_video=uname, cap=cap,
                prefix=f"scail2/{args.run}_{seg}_s{seed}",
                prompt=prompt,
                # 负向提示词：节点 4 实际接进 WanSCAILToVideo(114/182)，喂文本
                # 即生效——旧版写死 "" 等于白白浪费一路条件（2026-11-08 修复）
                negative=recipe.get("negative_prompt", ""),
                force_rate=recipe.get("force_rate", 20),
                fps=recipe.get("fps", 20),
                ref_image=ref,
            )
            # 分模块：每段工作流 JSON 落盘（绑定参考图=上传照片+姿态视频+本段提示词）
            if workflow_dir:
                (workflow_dir / f"scail_{seg}_s{seed}.json").write_text(
                    json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
            # seed 覆盖：节点 19（首窗口）与 183（循环窗口）
            for node in ("19", "183"):
                if node in graph:
                    graph[node]["inputs"]["noise_seed"] = seed
            pid = api.submit(graph)
            key = f"{seg}_s{seed}"
            mapping[key] = {"prefix": f"scail2/{args.run}_{seg}_s{seed}", "pid": pid,
                            "seg": seg, "seed": seed, "ts": time.time()}
            print(f"[{key}] {cap}帧 -> {pid}", flush=True)
            time.sleep(1)
    (run_dir / "map.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print(f"已提交 {len(mapping)} 段，映射: {run_dir / 'map.json'}", flush=True)


def check_pid(pid: str, started: dict, stall_minutes: int = 360):
    """⑥ ComfyUI 看门狗：pending pid 健康检查。

    返回 None = 正常；字符串 = 失败原因（/history error，或确认 pid ∈
    /queue 的 queue_running 且运行超 stall_minutes 分钟 → POST /interrupt）。

    2026-11-06 实测：SCAIL 单段合法耗时 20~260 分钟，旧阈值 40 分钟把正在
    采样的任务当死锁打断（新照片全链路首跑 idle1_s1/s2 就是这样被误杀）。
    阈值提到 360 分钟，只拦真正挂死的任务。
    """
    try:
        h = json.load(urllib.request.urlopen(
            config.SERVER + "/history/" + pid, timeout=30))
    except Exception:
        return None
    entry = h.get(pid)
    if entry and entry.get("status", {}).get("status_str") == "error":
        return "ComfyUI 执行 error"
    try:
        q = json.load(urllib.request.urlopen(config.SERVER + "/queue", timeout=30))
    except Exception:
        return None
    running = {it[1] for it in q.get("queue_running", [])
               if isinstance(it, (list, tuple)) and len(it) > 1}
    if pid not in running:
        started.pop(pid, None)
        return None
    t0 = started.setdefault(pid, time.time())
    if time.time() - t0 > stall_minutes * 60:
        try:
            urllib.request.urlopen(urllib.request.Request(
                config.SERVER + "/interrupt", data=b"", method="POST"), timeout=30)
        except Exception:
            pass
        return f"运行超 {stall_minutes} 分钟疑似死锁，已 POST /interrupt"
    return None


def cmd_collect(args):
    run_dir = ROOT / "runs" / args.run
    map_path = run_dir / "map.json"
    if not map_path.is_file():
        sys.exit(f"map.json 不存在: {map_path}")  # 提交记录缺失仍判死（软失败仅限任务级）
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    pending = dict(mapping)
    started = {}   # pid -> 首次观察到 running 的时刻（看门狗计时基准）
    failed = {}    # seg_sN -> 失败原因
    deadline = time.time() + args.timeout
    while pending and time.time() < deadline:
        for key, info in list(pending.items()):
            # 只认这个任务自己的产出：history 精确 outputs；history 被清空才按
            # 最大版本号兜底。绝不看 _00001_ 是否存在（2026-11-05 实测：
            # 所有重掷任务真实产出是 _00002_，旧逻辑拿到的全是上一版旧视频）。
            resolved = api.resolve_job_output(
                info["pid"], info.get("prefix", ""), info.get("ts", 0))
            src = Path(resolved) if resolved else None
            if src and src.is_file() and src.stat().st_size > 10000:
                ws = face_widths(src)
                miss = int(np.isnan(ws).sum())
                print(f"[{key}] {src.name} 脸宽极差 {swing(ws):.1f}% 漏检{miss}", flush=True)
                del pending[key]
                continue
            reason = check_pid(info["pid"], started)
            if reason:
                failed[key] = reason
                del pending[key]
                print(f"[{key}] 失败回收：{reason}", flush=True)
        if pending:
            time.sleep(60)
    # ⑥ 软失败：未完成项（超时/看门狗判死）写 missing.json 后正常退出（exit 0），
    # make_avatar 的 SCAIL 自愈（scail_heal）读它只重掷缺失项——原超时 exit 1
    # 会直接判死整条产线
    unfinished = {**failed, **{k: "collect 超时" for k in pending}}
    if unfinished:
        missing = [{"seg": mapping[k]["seg"], "seed": mapping[k]["seed"],
                    "prefix": mapping[k]["prefix"], "pid": mapping[k]["pid"],
                    "reason": v} for k, v in sorted(unfinished.items())]
        (run_dir / "missing.json").write_text(
            json.dumps(missing, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"未完成 {len(missing)} 项已写入 {run_dir / 'missing.json'}"
              "（软失败，交产线自愈）", flush=True)
        return
    print("全部完成", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["submit", "collect"])
    ap.add_argument("--segments", default="idle1,idle2,idle3,greet,talk")
    ap.add_argument("--seeds", default="1,2")
    ap.add_argument("--run", required=True)
    ap.add_argument("--recipe", default="scail_stability.json")
    ap.add_argument("--driver-dir", default="",
                    help="覆盖驱动视频目录（默认 E:/Comfyui）。用于裁剪后的静区间驱动，"
                         "目录内文件仍须按 <段名>.mp4 命名；不覆盖原驱动文件")
    ap.add_argument("--ref-image", default="",
                    help="覆盖参考图（默认保持底快照里的图）。照片→数字人产线用："
                         "换任意用户照片生成同套动作的新形象")
    ap.add_argument("--appearance-file", default="",
                    help="外观描述文件（视觉 LLM 看上传照片写的英文外观句；空=用配方默认指代）")
    ap.add_argument("--workflow-dir", default="",
                    help="每段工作流 JSON 落盘目录（分模块：greet/idle/talk 各一个）")
    ap.add_argument("--timeout", type=int, default=OUTPUT_WAIT)
    args = ap.parse_args()
    if args.cmd == "submit":
        cmd_submit(args)
    else:
        cmd_collect(args)


if __name__ == "__main__":
    main()
