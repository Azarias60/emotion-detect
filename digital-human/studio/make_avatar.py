# -*- coding: utf-8 -*-
"""照片 → 数字人一键产线（docs/photo-to-digital-human-pipeline.md 的落地）。

输入一张用户照片 + 名字，串起既有产线脚本产出可上页的数字人：
  体检照片 → SCAIL 5 段×2seed（现有动作库 + 用户照片做参考）→ 选 seed →
  几何稳定化 → LTX 过渡 10 对 → QA（质量门/身份/过渡）→ 组装 avatar → 注册配对。

关键设计：
- **动作库复用**：SCAIL 姿态驱动只取驱动视频的动作，形象全由照片决定——
  新人物与鬼子共用 E:/Comfyui 的一套动作模板，无需重录；
- 各阶段 subprocess 调既有脚本（regen_scail/stabilize_segments/transition_queue/
  qa_segments/audit_identity/audit_transitions/build_avatar），本脚本只做编排；
- 进度落盘 progress.json（后端只读文件，进程死了状态仍可查）；
- QA 用新人物**基础段**做参考脸（不能用 data/avatars/<name>/full_imgs——
  那是组装产物，历史教训：混入过渡段会污染均值脸）。

用法（引擎 venv，项目根 digital-human/）：
    livetalking/venv/Scripts/python.exe studio/make_avatar.py \
        --photo <照片路径> --name <ascii_id> --display-name <中文名> \
        --progress-json <progress路径> [--dry-run]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "livetalking"
PY = str(ENGINE / "venv" / "Scripts" / "python.exe")
for p in (str(ENGINE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

STAGES = ["体检照片", "生成动作段", "挑选与稳定", "生成过渡", "质量检查", "组装上架"]
TOTAL = len(STAGES)


class Progress:
    def __init__(self, path: Path, job_id: str, name: str):
        self.path = Path(path)
        self.base = {"job_id": job_id, "name": name, "total": TOTAL, "ts": 0}

    def write(self, stage_index: int, message: str, ok: bool | None = None):
        data = {**self.base, "stage": STAGES[min(stage_index, TOTAL - 1)],
                "stage_index": min(stage_index, TOTAL - 1),
                "message": message, "ts": time.time()}
        if ok is not None:
            data["ok"] = ok
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def run(cmd, **kw):
    """跑子进程，失败抛异常（message 带尾部输出）。"""
    r = subprocess.run([PY, *cmd], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **kw)
    if r.returncode != 0:
        tail = ((r.stdout or "") + (r.stderr or ""))[-800:]
        raise RuntimeError(f"命令失败({' '.join(cmd[:3])}...): {tail}")
    return r.stdout


def stage_inspect(photo: Path, prog: Progress):
    img_ok = photo.is_file() and photo.suffix.lower() in (".jpg", ".jpeg", ".png")
    if not img_ok:
        raise RuntimeError("照片文件不存在或格式不支持（需要 jpg/png）")
    img = cv2.imread(str(photo))
    if img is None:
        raise RuntimeError("照片无法读取（可能已损坏）")
    h, w = img.shape[:2]
    if w < 480 or h < 720:
        raise RuntimeError(f"照片分辨率太小（{w}x{h}），建议 720×1080 以上的半身正面照")
    sys.path.insert(0, str(ENGINE))
    from studio.qa_segments import load_detector
    det = load_detector()
    r = det.get_detections_for_batch(np.array([img]))
    if not r or r[0] is None:
        raise RuntimeError("照片里没有检测到正脸——请换一张光线充足、正脸清晰的半身照")
    x1, y1, x2, y2 = r[0]
    if (x2 - x1) < w * 0.08:
        raise RuntimeError("照片里的人脸太小——请离镜头近一些，拍半身照")
    return f"照片合格（{w}×{h}，已检出正脸）"


# 一致性驱动方案（2026-10-22 终版攻坚结论）：三个待机段共用 idle2 驱动
# （不同驱动的拍摄距离差异会让背景尺度分成两派，LTX 过渡被迫运镜弥合——
# 鬼子旧素材 1.28x 失配的根因）；greet/talk 用各自驱动（与 idle2 距离一致）
# 统一动作模板（2026-10-29 用户拍板）：全部数字人用同一套标准 6 段动作库，
# 每段各自的驱动视频（曾把 idle1/2/3 全映射到同一个 idle2.mp4 → 三个 idle
# 完全雷同）。动作库只提供姿态，人物身份来自上传照片（SCAIL 已验证）。
# 驱动源切换（2026-11-04 用户拍板）：改用 zzf-character——zzf 是页面播放
# 最顺滑的数字人，其动作段全程姿态平稳、无张嘴干扰；原 guizi 库的 greet
# 驱动含张嘴帧（2026-11-04 实测迁移后 greet 段张嘴）。
DRIVER_SRC = ROOT / "assets" / "materials"   # 2026-11-07 换短动作素材库（720x1280/30fps，全部单窗直出）；旧源 zzf-character 备在原处
DRIVER_MAP = {"idle1": "idle1.mp4", "idle2": "idle2.mp4", "idle3": "idle3.mp4",
              "greet": "greet.mp4", "talk": "talk.mp4",
              "comfort": "comfort.mp4", "encourage": "encourage.mp4"}
# 动作模板（2026-11-07 扩到 7 段，对齐 zzf 本体动作库规模）；过渡对挂点见 TRANS_PAIRS
TEMPLATE_SEGS = ("idle1", "idle2", "idle3", "greet", "talk", "comfort", "encourage")
TRANS_PAIRS = ["idle2 to idle3", "idle3 to idle1", "idle1 to greet", "greet to talk",
               "idle1 to talk", "talk to idle1", "talk to greet", "idle1 to idle2",
               "greet to idle1", "talk to talk",
               "idle1 to comfort", "comfort to idle1",
               "idle1 to encourage", "encourage to idle1"]


def build_consistent_driver_dir(work: Path) -> Path:
    d = work / "drivers"
    d.mkdir(parents=True, exist_ok=True)
    import shutil
    for tgt, src in DRIVER_MAP.items():
        src_path = Path(src) if Path(src).is_absolute() else DRIVER_SRC / src
        shutil.copy2(src_path, d / f"{tgt}.mp4")
    return d


def vision_appearance(photo: Path) -> "str | None":
    """视觉 LLM 看上传照片 → 写英文外观句（身份锚，组合进每段提示词）。

    GLM-4V-Flash（智谱官方免费视觉模型）；未配 VISION_API_KEY 或失败 → None，
    产线回退配方的中性指代（身份靠节点 30 参考图）。"""
    import base64
    import urllib.request

    key = os.environ.get("VISION_API_KEY", "")
    if not key or not photo.is_file():
        return None
    b64 = base64.b64encode(photo.read_bytes()).decode()
    base = os.environ.get("VISION_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    model = os.environ.get("VISION_MODEL", "glm-4v-flash")
    instruction = (
        "Describe this person's appearance for a video-generation prompt: hair "
        "style and color, face, clothing, body shape. One English sentence as a "
        "subject phrase starting with 'A young woman' or 'A young man', 25~40 "
        "words. Do not describe the background. Output only the sentence."
    )
    try:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + b64}},
                {"type": "text", "text": instruction},
            ]}],
            "temperature": 0.5,
            "max_tokens": 200,
        }).encode("utf-8")
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return text.strip().strip('"“”') or None
    except Exception:
        return None


def stage_scail(name: str, photo: Path, prog: Progress, dry: bool, work: Path,
                segs=TEMPLATE_SEGS):
    run_name = f"{name}_g"
    driver_dir = build_consistent_driver_dir(work)
    # ③ 参考图唯一名：ensure_input_file 对 input 已有同名文件直接跳过复制
    # （api.py），而后端上传统一命名 photo.jpg——不换名的话新任务会静默沿用
    # ComfyUI input 里的上一次旧照片（实测 sha1 不同）。ref_<name><suffix>
    # 每个人物唯一，根治同名跳过用错源。
    work.mkdir(parents=True, exist_ok=True)
    ref_path = work / f"ref_{name}{photo.suffix.lower()}"
    if not (ref_path.exists() and os.path.samefile(photo, ref_path)):
        shutil.copy2(photo, ref_path)
    # ⑨ 视觉外观描述（GLM-4V-Flash 免费）：看上传照片写英文外观句，组合进
    # 每段提示词——文本侧也锚定照片真人（参考图之外的强化锁）；未配 key 跳过
    appearance = None if dry else vision_appearance(ref_path)
    if appearance:
        (work / "appearance.txt").write_text(appearance, encoding="utf-8")
        prog.write(1, "AI 已读照片并写好人物外观描述")
    run(["studio/regen_scail.py", "submit",
         "--segments", ",".join(segs),
         "--seeds", "1,2", "--run", run_name, "--ref-image", str(ref_path),
         "--driver-dir", str(driver_dir),
         "--appearance-file", str(work / "appearance.txt") if appearance else "",
         "--workflow-dir", str(ROOT / "runs" / "make_avatar" / name / "workflows")])
    return run_name, ref_path, driver_dir


def _run_maps(name: str):
    """本人物所有 SCAIL 提交记录（主 run + 自愈子 run）的 map.json。"""
    dirs = [ROOT / "runs" / f"{name}_g"]
    dirs += sorted((ROOT / "runs").glob(f"{name}_g_h*"))
    for d in dirs:
        mp = d / "map.json"
        if mp.is_file():
            yield d, json.loads(mp.read_text(encoding="utf-8"))


def _candidates(name: str, seg: str):
    """按 map.json 的 pid 精确解析每个 seed 的**实际产出**（history outputs）。

    2026-11-05 根因修复：旧实现 glob `_00001_.mp4`，而重掷任务的真实产出
    是 _00002_/_00003_，导致整条产线一直在用上一版旧视频（内容差 15~22 灰度）。
    """
    sys.path.insert(0, str(ROOT / "generation"))
    from comfy import api
    out, seen = [], set()
    for _d, mapping in _run_maps(name):
        for info in mapping.values():
            if info.get("seg") != seg:
                continue
            p = api.resolve_job_output(
                info["pid"], info.get("prefix", ""), info.get("ts", 0))
            if not p or p in seen:
                continue
            seen.add(p)
            out.append((int(info["seed"]), Path(p)))
    return out


def stage_select(name: str, prog: Progress, segs=TEMPLATE_SEGS):
    """收集各 seed 产出（s1/s2 及自愈轮补的 s101/s201…全量候选），按脸宽极差+
    漏检选优，组 staging 目录。只使用任务 history 报出的真实产出。"""
    stage_dir = ROOT / "runs" / "make_avatar" / name / "segments"
    stage_dir.mkdir(parents=True, exist_ok=True)
    from studio.regen_scail import face_widths
    chosen: dict[str, Path] = {}
    picked = []
    for seg in segs:
        best = None
        for _seed, src in _candidates(name, seg):
            ws = face_widths(src)
            v = ws[~np.isnan(ws)]
            if len(v) < 10:
                continue
            swing = 100 * (v.max() - v.min()) / v.mean()
            miss = int(np.isnan(ws).sum())
            score = swing + miss * 5
            if best is None or score < best[0]:
                best = (score, src, swing, miss)
        if best is None:
            raise RuntimeError(f"{seg} 所有 seed（含自愈重掷）都没有合格产出")
        shutil.copy2(best[1], stage_dir / f"{seg}.mp4")
        chosen[seg] = best[1]
        picked.append(f"{seg}(极差{best[2]:.1f}%)")
    # talk 之外的备用 talk 产出 → talk2；一个都没有则复用已选 talk
    if "talk" in segs:
        alts = [p for _seed, p in _candidates(name, "talk") if p != chosen.get("talk")]
        shutil.copy2(alts[0] if alts else stage_dir / "talk.mp4",
                     stage_dir / "talk2.mp4")
    return f"选优完成：{'、'.join(picked)}", stage_dir



def _edge_jitter(video: Path) -> float:
    """边缘背景带（人物不触边）逐帧差中位数，与 stabilize_background 同口径。"""
    cap = cv2.VideoCapture(str(video))
    prev, ds = None, []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, (352, 640)), cv2.COLOR_BGR2GRAY).astype(np.float32)
        band = np.zeros(g.shape, bool)
        band[:90, :] = True
        band[:, :55] = True
        band[:, -55:] = True
        if prev is not None:
            ds.append(float(np.abs(g[band] - prev[band]).mean()))
        prev = g
    cap.release()
    return float(np.median(ds)) if ds else 0.0


def _segment_stability(stage_dir: Path):
    """各基础段脸宽极差 + 段间中位脸宽差。

    2026-11-05 实测：最新 SCAIL 产物脸宽中位数 101~103px（段间差 <2%）、
    内部极差 3~8%——已经比旧库干净得多，此时再跑几何校正只会把好画面
    二次 warp 坏。只有明显漂移（>8%）或段间尺度差（>5%）才需要校正。
    """
    from studio.regen_scail import face_widths
    stats = {}
    for p in sorted(stage_dir.glob("*.mp4")):
        if " to " in p.stem:
            continue
        ws = face_widths(p)
        v = ws[~np.isnan(ws)]
        if len(v) < 10:
            continue
        stats[p.stem] = (100 * (v.max() - v.min()) / v.mean(), float(np.median(v)))
    return stats


def gate_transitions(stage_dir: Path, peak_gate: float = 10.0):
    """终检门：返回尖峰>gate 的过渡配对名列表。"""
    import cv2 as _cv2
    bad = []
    for v in sorted(stage_dir.glob("* to *.mp4")):
        cap = _cv2.VideoCapture(str(v))
        fr = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            fr.append(f)
        cap.release()
        if len(fr) < 10:
            bad.append(v.stem)
            continue
        diffs = [float(np.abs(fr[i].astype(int) - fr[i - 1].astype(int)).mean())
                 for i in range(1, len(fr))]
        if max(diffs) > peak_gate:
            bad.append(v.stem)
    return bad


def scail_heal(name: str, run_name: str, ref_path: Path, driver_dir: Path,
               prog: Progress, max_rounds: int = 2):
    """⑦ SCAIL 缺段自愈：collect 软失败后（runs/<run>/missing.json），只对仍无
    产出文件的缺失 (seg,seed) 用 seed+100×轮 重掷，≤max_rounds 轮——保证
    stage_select 前 5 段各有 ≥1 个合格 seed。

    重掷按 seed 分组提交到独立子 run：regen_scail submit 的 map.json 每次提交
    整写，同 run 复用会丢映射；产出文件名含 seed 落回 scail2/，由 stage_select
    的 glob（{name}_g_{seg}_s*_00001_.mp4）统一回收。
    """
    sys.path.insert(0, str(ROOT / "generation"))
    import config
    scail_out = Path(config.COMFY_OUTPUT) / "scail2"
    heal_runs: list = []
    for rnd in range(1, max_rounds + 1):
        run_ids = [run_name, *heal_runs]
        if not any((ROOT / "runs" / rid / "missing.json").is_file() for rid in run_ids):
            return  # 没有失败记录，无需自愈
        miss_pairs: list = []
        for rid in run_ids:
            mp = ROOT / "runs" / rid / "missing.json"
            if not mp.is_file():
                continue
            for m in json.loads(mp.read_text(encoding="utf-8")):
                key = (m["seg"], int(m["seed"]))
                if key in miss_pairs:
                    continue
                # 后续轮已补出产出的不再算缺
                if list(scail_out.glob(f"{name}_g_{m['seg']}_s{m['seed']}_*.mp4")):
                    continue
                miss_pairs.append(key)
        if not miss_pairs:
            return  # missing 记录里的项都已补出
        prog.write(1, f"SCAIL 自愈第{rnd}轮：重掷缺失 {len(miss_pairs)} 项"
                      f"（{[f'{s}_s{d}' for s, d in miss_pairs]}，seed+100×轮）…")
        # 按 seed 分组提交，避免 submit 的 segs×seeds 笛卡尔积多生成
        by_seed: dict = {}
        for seg, seed in miss_pairs:
            by_seed.setdefault(seed + 100 * rnd, []).append(seg)
        new_runs = []
        for new_seed, segs in sorted(by_seed.items()):
            sub_run = f"{run_name}_h{rnd}s{new_seed}"
            run(["studio/regen_scail.py", "submit",
                 "--segments", ",".join(sorted(set(segs))), "--seeds", str(new_seed),
                 "--run", sub_run, "--ref-image", str(ref_path),
                 "--driver-dir", str(driver_dir)])
            new_runs.append(sub_run)
        heal_runs.extend(new_runs)
        for sub_run in new_runs:
            run(["studio/regen_scail.py", "collect", "--run", sub_run,
                 "--timeout", "21600"])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", required=True)
    ap.add_argument("--name", required=True, help="ASCII id")
    ap.add_argument("--display-name", default="")
    ap.add_argument("--progress-json", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-select", action="store_true",
                    help="跳过生成阶段，从选优开始跑（含肤色/插帧/QA/上架）——QA 不过换种子重掷后用")
    ap.add_argument("--skip-transitions", action="store_true",
                    help="跳过过渡生成（配合 --from-select：过渡已产出并过检时用）")
    ap.add_argument("--segments", default=",".join(TEMPLATE_SEGS),
                    help="本次生成的动作段清单（默认=7 段模板；测试可只传 greet）")
    ap.add_argument("--resume", action="store_true",
                    help="SCAIL 任务已提交且产出完毕：跳过体检/提交，从回收阶段恢复")
    ap.add_argument("--reuse-run", default="",
                    help="复用已提交的 SCAIL run（如 verify_cand2_g）：跳过提交，直接 collect+自愈后继续全链路")
    args = ap.parse_args()

    photo = Path(args.photo)
    prog = Progress(Path(args.progress_json), args.name, args.display_name or args.name)
    name = args.name

    def tick(i, msg, dry_sleep=0.6):
        prog.write(i, msg)
        print(f"[{STAGES[min(i, TOTAL-1)]}] {msg}", flush=True)
        if args.dry_run:
            time.sleep(dry_sleep)

    try:
        # 0 体检
        if not args.resume:
            tick(0, "正在检查照片…")
            msg = stage_inspect(photo, prog)
            tick(0, msg + ("（dry-run）" if args.dry_run else ""))

        # ④ work 无条件定义（原代码只在非 resume 分支里赋值，--resume 时
        #    stage_scail(..., work) 直接 NameError）；resume 且六段基础段全在
        #    → 跳过阶段1/2（select 会重拷旧产物、stabilize/unify 二次处理劣化
        #    已处理过的画面，故两阶段一起跳，直接从阶段3过渡续跑）
        work = ROOT / "runs" / "make_avatar" / name
        segs = tuple(x.strip() for x in args.segments.split(",") if x.strip())
        seg_names = segs + (("talk2",) if "talk" in segs else ())
        skip_gen = args.resume and all(
            (work / "segments" / f"{s}.mp4").is_file() for s in seg_names)

        if args.dry_run:
            tick(1, "（dry-run）跳过生成"
                 + ("（resume：六段基础段已齐，正式跑将跳过阶段1/2）" if skip_gen else ""))
            tick(2, "（dry-run）跳过挑选")
            tick(3, "（dry-run）跳过过渡")
            tick(4, "（dry-run）质检通过")
            tick(5, "（dry-run）组装完成")
            prog.write(TOTAL, "dry-run 完成", ok=True)
            return

        # 1 SCAIL 生成 + ⑦ 缺段自愈（collect 软失败不再判死产线）
        run_name = args.reuse_run or f"{name}_g"
        if not skip_gen and not args.from_select:
            if args.reuse_run:
                tick(1, f"复用已提交的 SCAIL 任务 {args.reuse_run}，等待回收…", dry_sleep=0)
                work.mkdir(parents=True, exist_ok=True)
                ref_path = work / f"ref_{name}{photo.suffix.lower()}"
                driver_dir = build_consistent_driver_dir(work)
                # stage_select 从 runs/<name>_g/map.json 找任务；复用 run 时
                # 若 run 名与 name 不同，把 map 复制过来。
                src_map = ROOT / "runs" / args.reuse_run / "map.json"
                dst_dir = ROOT / "runs" / f"{name}_g"
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst_map = dst_dir / "map.json"
                if src_map.resolve() != dst_map.resolve():
                    shutil.copy2(src_map, dst_map)
            else:
                tick(1, "正在用照片生成 5 个动作段（SCAIL 单段 20~260 分钟，请勿关闭）…", dry_sleep=0)
                run_name, ref_path, driver_dir = stage_scail(name, photo, prog,
                                                             args.dry_run, work, segs)
            prog.write(1, "动作段生成完成，正在回收…")
            run(["studio/regen_scail.py", "collect", "--run", run_name, "--timeout", "43200"])
            scail_heal(name, run_name, ref_path, driver_dir, prog)
        else:
            tick(1, "resume：六段基础段已齐，跳过 SCAIL 生成与回收", dry_sleep=0)

        # 2 选优 + 稳定化
        if skip_gen:
            stage_dir = work / "segments"
            tick(2, "resume：跳过挑选与稳定化，直接续跑过渡", dry_sleep=0)
        else:
            tick(2, "正在挑选最佳动作段并做稳定性体检…", dry_sleep=0)
            msg, stage_dir = stage_select(name, prog, segs)

            # 几何校正只在确有漂移时才做：最新 SCAIL 产物脸宽中位 101~103px、
            # 段间差 <2%，内部极差 3~8%——对好素材再做 warp 反而会糊/抖。
            stats = _segment_stability(stage_dir)
            swings = [s[0] for s in stats.values()]
            meds = [s[1] for s in stats.values()]
            spread = (100 * (max(meds) - min(meds)) / (sum(meds) / len(meds))
                      if meds else 0.0)
            if (swings and max(swings) > 8.0) or spread > 5.0:
                run(["studio/stabilize_segments.py", "--segments", str(stage_dir),
                     "--apply"])
                prog.write(2, f"检测到漂移（最大极差 {max(swings):.1f}%、段间尺度差 "
                              f"{spread:.1f}%），已做几何校正")
            else:
                prog.write(2, f"动作段几何已稳定（最大极差 {max(swings):.1f}%、段间 "
                              f"尺度差 {spread:.1f}%），跳过校正")

            # 肤色统一：SCAIL 与 LTX 渲染域不同（脸部 BGR 差最多 10 色阶）
            run(["studio/unify_color.py", "--segments", str(stage_dir), "--apply"])

            # 背景锁定必跑（2026-11-08 用户实看背景仍晃）：边缘带指标（左右
            # 55px）测不到人物周边拱门立柱区的晃动，门槛跳过会漏。板替换对
            # 全背景生效（人物掩膜外整块换成时间中位背景板），有动作段后跑。
            jitters = {p.stem: _edge_jitter(p) for p in sorted(stage_dir.glob("*.mp4"))
                       if " to " not in p.stem}
            worst = max(jitters.values()) if jitters else 0.0
            run(["studio/stabilize_background.py", "--segments", str(stage_dir),
                 "--only-action", "--passes", "2", "--th", "22", "--apply"])
            prog.write(2, f"背景已锁定背景板（锁定前边缘抖动最大 {worst:.2f}）")

            # 20fps -> 25fps 真插帧（build_avatar 的 fps=25 是复制帧，会顿）
            try:
                run(["studio/interpolate_rife.py", "--segments", str(stage_dir),
                     "--apply"])
                prog.write(2, "RIFE 插帧完成（动作段 25fps，非复制帧）")
            except Exception as exc:
                prog.write(2, f"RIFE 插帧失败，保留 20fps（组装时补帧）：{exc}")

            # 腿部抖动修复（2026-11-08）：驱动素材头到膝取景（脚不入镜），
            # SCAIL 逐帧重绘下半身 → 轮廓/脚高频颤动。运动门控时间低通只修
            # "原地颤动"像素（hf≫slow），挥手/手势不受影响；必须在过渡生成
            # 之前跑——过渡的端点取自基础段末帧，端点必须已是修后版本。
            run(["studio/stabilize_legs.py", "--segments", str(stage_dir),
                 "--only", "base", "--apply"])
            tick(2, msg)

        # 3 过渡（⑦ 自愈循环 ≤3 轮：seed 801/802/803，缺什么/超标什么重掷什么；
        #    collect 软失败（missing.json）与 gate 尖峰超标都汇入 remaining。
        #    ② 配方换中性版：默认 guizi 配方的 pos 写死了男像/夜景，会把鬼子
        #    身份从过渡段拉回来）
        # 过渡对自适应：只生成两端都在本次段清单里的对（单段模式自然为空）
        pairs = [] if args.skip_transitions else [
            p for p in TRANS_PAIRS
            if all(x in segs for x in p.split(" to "))]
        if not pairs:
            prog.write(3, "单段模式：跳过过渡")
            tick(3, "单段模式：无过渡对，跳过过渡生成", dry_sleep=0)
        t_run = f"{name}_t"
        todo = list(pairs)
        remaining = []
        # 过渡 25 帧（8k+1 合法档，=1.00s@25fps，用户定档）：旧 81 帧≈3.2s，
        # 触发动作后要先看完 3 秒过渡才开始演——太拖。25 帧给大姿势差留了
        # 足够中间帧（比 17 帧稳），观感仍跟手；再快 17 帧(0.68s)/9 帧(0.36s)。
        for rnd, seed in enumerate((801, 802, 803), start=1):
            if not todo:
                break  # 单段模式：无过渡对，直接进 QA
            run_id = t_run if rnd == 1 else f"{t_run}r{rnd}"
            # 过渡工作流 JSON 由 transition_queue 自动落 run_dir/workflows/
            run(["studio/transition_queue.py", "submit", "--pairs", *todo,
                 "--run", run_id, "--frames", "25", "--fps", "25", "--fast",
                 "--seed", str(seed), "--seg-dir", str(stage_dir),
                 "--recipe", "ltx_transition_neutral.json"])
            run(["studio/transition_queue.py", "collect", "--run", run_id,
                 "--timeout", "10800", "--seg-dir", str(stage_dir)])
            # LTX 对大姿势差（垂手↔挥手）会把渐变做成一帧瞬移——光流 morph 展开
            run(["studio/morph_fix.py", "--segments", str(stage_dir), "--apply"])
            # 尾段收尾沉降裁剪（2026-11-05 全帧核验确认 greet→idle1 有 21 帧
            # 真冻结）：只剪末尾低运动沉降，保留中间运动帧；端点差 >6 不剪；
            # <=60 帧视为已裁，幂等。中间帧仍不做任何剪帧/定格修剪。
            run(["studio/trim_transition_tail.py", "--segments", str(stage_dir), "--apply"])
            # ⑨ 过渡尖峰/突发修复（2026-11-08 e2egreet01 实测教训）：LTX 对大
            # 姿势差会出孤立尖峰帧（躯体扭曲 1~2 帧）和尾部抖动突发（帧差 9~21
            # 连续 5~7 帧），换 seed 重掷 3 轮均无法避免（结构性）。孤立尖峰用
            # 前后帧混合替换，突发/末帧跳变贪心摘除源帧——必须在终检门之前跑，
            # 否则门限降级上架会把坏过渡带进成品。
            run(["studio/fix_spike_frames.py", "--segments", str(stage_dir), "--apply"])
            # 腿部稳定化（过渡段）：LTX 过渡的腿部运动多为姿势渐变（slow 主导）
            # 门控自然低触发，但 talk to talk 这类仍有幻觉颤动；在终检门之前跑，
            # 保证门测的是成品。
            run(["studio/stabilize_legs.py", "--segments", str(stage_dir),
                 "--only", "transitions", "--apply"])
            # 注意：fix_spike 只能修"时间域"缺陷（帧差尖峰/突发）。LTX 还有
            # "空间域"单帧伪影（手部糊化、腰髋雾带——帧差正常、只有视觉能
            # 发现，e2egreet01 实测 2 处），上线前建议对每段抽帧做一次视觉
            # 抽检（GLM 视觉模型），发现坏帧用邻帧替换。
            # 终检门：尖峰>10 ∪ 产出仍缺失（missing.json 记录且文件确实没落盘）
            rest = set(gate_transitions(stage_dir))
            miss_path = ROOT / "runs" / run_id / "missing.json"
            if miss_path.is_file():
                rest |= {m["pair"] for m in json.loads(miss_path.read_text(encoding="utf-8"))
                         if not (stage_dir / f"{m['pair']}.mp4").is_file()}
            rest &= set(pairs)
            if not rest:
                remaining = []
                break
            remaining = sorted(rest)
            if rnd < 3:
                prog.write(3, f"过渡自愈第{rnd}轮后仍差 {len(remaining)} 条"
                              f"（{remaining}），换 seed {seed + 1} 重掷…")
                todo = remaining
        if remaining:
            # 3 轮后仍缺：降级上架（缺过渡=动作切换处跳变），如实记录不掩盖
            prog.write(3, f"过渡 3 轮自愈后仍差 {len(remaining)} 条：{remaining}"
                          "（带残差继续：缺过渡=动作切换处跳变的降级形态）")
        tick(3, "过渡生成完成")

        # 4 QA
        tick(4, "正在进行质量检查…", dry_sleep=0)
        qa_out = ROOT / "runs" / "make_avatar" / name
        # 注意：qa_segments/audit_identity 的 --out 都是文件路径，绝不能共用
        # 同名前缀——曾把 "qa" 同时当文件(qa_segments)和目录(audit)用，第二轮
        # 起写入即 PermissionError（2026-11-08 实锤）。各用独立文件名。
        run(["studio/qa_segments.py", "--segments", str(stage_dir),
             "--out", str(qa_out / "qa_report.json")])
        ref = "idle1+idle2+idle3+greet+talk+talk2"
        # ⑧ refs 追加 guizi：显式审计"是否串成鬼子"（data/avatars/guizi 实测在盘），
        # 输出进 identity.json 供人工/视觉闸复核
        run(["studio/audit_identity.py", "--segments", str(stage_dir),
             "--expect", ref, "--refs", f"{ref},my_avatar,yy,guizi",
             "--out", str(qa_out / "identity.json")])
        tick(4, "质量检查完成")

        # 5 组装 + 注册
        tick(5, "正在组装数字人并上架…", dry_sleep=0)
        run(["studio/build_avatar.py", "--segments", str(stage_dir),
             "--avatar-id", name, "--display-name", args.display_name or name])
        presets_path = ENGINE / "data" / "presets.json"
        presets = json.loads(presets_path.read_text(encoding="utf-8")) if presets_path.is_file() else []
        if not any(p.get("id") == name for p in presets):
            presets.append({"id": name, "name": args.display_name or name,
                            "avatar": name, "voice": "default",
                            "note": "照片生成 · 默认音色"})
        presets_path.write_text(json.dumps(presets, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        tick(5, f"完成！数字人「{args.display_name or name}」已上架")
        prog.write(TOTAL, f"数字人「{args.display_name or name}」制作完成，点「刷新」即可选择", ok=True)
    except Exception as error:
        prog.write(0, f"制作失败：{error}", ok=False)
        print(f"失败：{error}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
