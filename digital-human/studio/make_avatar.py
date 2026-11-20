# -*- coding: utf-8 -*-
"""照片 → 数字人一键产线（docs/photo-to-digital-human-pipeline.md 的落地）。

输入一张用户照片 + 名字，串起既有产线脚本产出可上页的数字人：
  体检照片 → SCAIL 动作段（每段一次）→ 回收 → LTX 过渡 → QA 门限 → 组装 avatar → 注册配对。

关键设计：
- **动作库复用**：SCAIL 姿态驱动只取驱动视频的动作，形象全由照片决定——
  新人物与 zzf 共用 assets/materials 的一套动作模板，无需重录；
- **2026-11-09 起只做调色，绝不改画面内容**（用户拍板：“可以进行调色，但绝对
  不要对画面内容进行裁切和修改”）：
  · 允许：unify_color——整帧 Reinhard LAB + 脸部常量偏移，只改颜色不改构图，
    帧数/动作/姿态一律不动（治 SCAIL 与 LTX 渲染域不同造成的接缝明暗台阶）；
  · 禁止：插帧、光流修瞬移、剪尾段定格、稳腿、背景板替换、几何校正、肤色
    以外的任何像素操作。
  SCAIL 直接按 25fps 出片（配方 force_rate/fps=25），与引擎播放帧率一致，
  组装时既不插帧也不补帧。产物有问题靠 QA 门限**重新生成**：
  基础段门（嘴没闭住/背景晃动>0.6/定格≥10帧/脸宽极差>12%）不过就换 seed
  重掷并连带重跑受影响的过渡；过渡门（尖峰>10/黑帧/亮度崩塌/定格）同理。
- **调色顺序有讲究**：基础段必须先调成最终颜色，再生成过渡——过渡的端点帧
  取自基础段末帧，基础段颜色没定，接缝就会出现明暗台阶。所以调色在阶段 2
  （基础段就位后）和阶段 3（过渡收完后）各跑一次。
- 各阶段 subprocess 调既有脚本（regen_scail/transition_queue/qa_segments/
  audit_identity/build_avatar），本脚本只做编排；
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


def run(cmd, check=True, **kw):
    """跑子进程，默认失败抛异常（message 带尾部输出）。

    check=False 用于"失败也是有效结论"的检查类命令：qa_segments 在有段不合格
    时以非零码退出，但报告 JSON 照常落盘——门限逻辑读 JSON 判定，退出码绝不能
    变成产线杀手（2026-11-10 e2e 实锤：终检 2 段嘴闭差 0.01 直接把整条产线
    砸成"制作失败"，此前 14 条过渡全白跑）。
    """
    r = subprocess.run([PY, *cmd], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **kw)
    if check and r.returncode != 0:
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
# 过渡对全量集（2026-11-13 补齐）：状态机可达的每一对都生成精确过渡，
# 不再有"泛化借帧（起点姿势不符 → 人物瞬移）"和"硬切（姿势突变）"。
# 分四组：
#   ① 待机环      idle2→idle3→idle1→idle2 + idle2⇄idle1 + idle3→idle2 + idle1→idle3
#   ② 待机→说话   idle1/idle2/idle3 → talk（70% 概率的说话开场从 idle2 出发）
#   ③ 待机→动作   idle1/idle2/idle3 → greet/comfort/encourage
#   ④ 说话↔动作   talk→greet/comfort/encourage、comfort/encourage→talk、
#                 talk→talk（talk2 复用 talk 的出口，段表里没有独立 talk2 段）
# 历史包袱：11-10 首版只有 14 条（环 + idle1 辐条），用户实测缺口的 4 条
# （idle3→idle2/idle2→idle1/idle2→talk/idle2→greet）和说话中情绪动作的
# 4 条（talk→comfort/encourage、comfort/encourage→talk）都是硬切或借帧。
TRANS_PAIRS = [
    # ① 待机环 + 星形拓扑（idle2 是中转站）
    "idle2 to idle3", "idle3 to idle1", "idle1 to idle2",
    "idle2 to idle1", "idle3 to idle2", "idle1 to idle3",
    # ② 待机 → 说话
    "idle1 to talk", "idle2 to talk", "idle3 to talk",
    # ③ 待机 → 动作
    "idle1 to greet", "idle2 to greet", "idle3 to greet",
    "idle1 to comfort", "idle2 to comfort", "idle3 to comfort",
    "idle1 to encourage", "idle2 to encourage", "idle3 to encourage",
    # ④ 说话 ↔ 动作 / 说话循环
    "greet to talk", "comfort to talk", "encourage to talk",
    "talk to greet", "talk to comfort", "talk to encourage",
    "talk to idle1", "talk to talk",
    # ⑤ 主待机段自循环（2026-11-13 用户要求"经常不停播 idle2，每次只有
    #    15% 概率播 idle1/idle3"）：idle2 播完 93 帧后经这条 1 秒自过渡
    #    回到自身起点，姿势差被摊开演完，而不是每 3.7 秒硬跳一次。
    "idle2 to idle2",
    # 动作 → 待机（ greet/comfort/encourage 共用 idle1 出口，其余 idle 由
    # 状态机同源泛化：出口姿势与目标 idle 无关，借帧安全）
    "greet to idle1", "comfort to idle1", "encourage to idle1",
]


def build_consistent_driver_dir(work: Path) -> Path:
    d = work / "drivers"
    d.mkdir(parents=True, exist_ok=True)
    import shutil
    for tgt, src in DRIVER_MAP.items():
        src_path = Path(src) if Path(src).is_absolute() else DRIVER_SRC / src
        # 2026-11-10 用户拍板：素材一律原样进入工作流，不做任何预处理
        # （此前的横向稳定化已按此要求撤线；质量靠阶段 2.5/4 的门限重掷保证）
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
    # 2026-11-09 用户拍板：每个只跑一次，有问题就重新跑（不再跑双 seed 选优，
    # 也不再对产物做任何后处理——插帧/光流/剪尾/稳腿/背景板全部撤掉）。
    # 帧率显式传 25（引擎原生），并在提交后校验落盘的工作流 JSON——2026-11-09
    # 踩坑：默认配方是 scail_stability.json 而非 scail_guizi.json，改了后者
    # 完全没生效，工作流静默按 20fps 提交。
    wf_dir = ROOT / "runs" / "make_avatar" / name / "workflows"
    run(["studio/regen_scail.py", "submit",
         "--segments", ",".join(segs),
         "--seeds", "1", "--run", run_name, "--ref-image", str(ref_path),
         "--driver-dir", str(driver_dir),
         "--force-rate", "25", "--out-fps", "25",
         "--appearance-file", str(work / "appearance.txt") if appearance else "",
         "--workflow-dir", str(wf_dir)])
    _verify_scail_fps(wf_dir, segs, expect=25)
    return run_name, ref_path, driver_dir


def _verify_scail_fps(wf_dir: Path, segs, expect: int = 25, seed: int = 1) -> None:
    """提交后校验：每段工作流的 node33 force_rate / node177 fps 必须等于 expect。

    防"改了配方却没生效"这类静默错误——帧率错了整套片子要么补帧顿挫、
    要么和引擎播放帧率不一致，必须提交时就知道，不能等出片才发现。
    seed 参数覆盖重掷提交（自愈/质量门重掷的 seed 是 110/120…，文件名随之变）。
    """
    bad = []
    for seg in segs:
        p = wf_dir / f"scail_{seg}_s{seed}.json"
        if not p.is_file():
            bad.append(f"{seg}(无工作流文件)")
            continue
        g = json.loads(p.read_text(encoding="utf-8"))
        fr = g.get("33", {}).get("inputs", {}).get("force_rate")
        fp = g.get("177", {}).get("inputs", {}).get("fps")
        if fr != expect or fp != expect:
            bad.append(f"{seg}(force_rate={fr}, fps={fp})")
    if bad:
        raise RuntimeError(f"SCAIL 提交帧率校验失败，应为 {expect}fps：{bad}")


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


def stage_select(name: str, prog: Progress, segs=TEMPLATE_SEGS, force_new: set | None = None):
    """收集各 seed 产出（s1/s2 及自愈轮补的 s101/s201…全量候选），按脸宽极差+
    漏检选优，组 staging 目录。只使用任务 history 报出的真实产出。

    force_new：这些段**强制用最新候选**（重掷出来的那一版），不参与比选。
    2026-11-09 用户点出的坑：按脸宽极差选优时，重掷段可能极差分稍高却被
    旧段比下去——旧段是已知不合格（背景晃/张嘴）的，选回去等于重掷白做，
    门限再检还是同一段。重掷的目的就是替换，所以必须强制采用。
    """
    stage_dir = ROOT / "runs" / "make_avatar" / name / "segments"
    stage_dir.mkdir(parents=True, exist_ok=True)
    force_new = force_new or set()
    from studio.regen_scail import face_widths
    chosen: dict[str, Path] = {}
    picked = []
    for seg in segs:
        cands = _candidates(name, seg)
        if seg in force_new and cands:
            # 取 seed 最大的那份（重掷 seed 110/120 > 首轮 1）
            seed, src = max(cands, key=lambda c: c[0])
            ws = face_widths(src)
            v = ws[~np.isnan(ws)]
            swing = 100 * (v.max() - v.min()) / v.mean() if len(v) >= 2 else float("nan")
            best = (0.0, src, swing, int(np.isnan(ws).sum()))
            picked.append(f"{seg}(重掷seed{seed}·极差{swing:.1f}%)")
        else:
            best = None
            for _seed, src in cands:
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
            picked.append(f"{seg}(极差{best[2]:.1f}%)")
        shutil.copy2(best[1], stage_dir / f"{seg}.mp4")
        chosen[seg] = best[1]
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


def _freeze_run(video: Path, th: float = 0.4) -> int:
    """人物区最长连续低运动帧数（定格检测）。

    2026-11-09：尾段沉降不再裁剪，改成“检测到就重掷”——所以这个度量从
    诊断变成门限。判据与已修复的 greet→idle1 案例一致：人物区（画面中部）
    相邻帧 MAD < 0.4 且连续 ≥10 帧，即认为该段有可见停顿。
    """
    cap = cv2.VideoCapture(str(video))
    prev, best, cur = None, 0, 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(f[150:1200, 140:560], cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            m = float(np.abs(g - prev).mean())
            if m < th:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        prev = g
    cap.release()
    return best


# 基础段门限（2026-11-09 用户拍板“模型出什么样就用什么样，有问题就重新跑”）
BG_SHAKE_GATE = 0.6      # 边缘背景带帧差中位数上限（旧库 1.1~3.3，好素材 0.10~0.27）
FREEZE_GATE = 10         # 人物区最长连续低运动帧数上限
SWING_GATE = 12.0        # 脸宽极差上限（%）


def gate_base_segments(stage_dir: Path, qa_json: Path | None = None) -> dict:
    """基础段体检：返回 {段名: [不合格原因]}。

    三项来源：qa_segments 的嘴/脸部门（读它落盘的 JSON）+ 背景晃动 + 定格。
    过渡段的门在 gate_transitions / transition_queue 的黑帧门里。
    """
    bad: dict[str, list[str]] = {}
    qa = {}
    if qa_json and qa_json.is_file():
        try:
            qa = json.loads(qa_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            qa = {}
    from studio.regen_scail import face_widths
    for p in sorted(stage_dir.glob("*.mp4")):
        if " to " in p.stem:
            continue
        reasons = []
        gates = (qa.get(p.stem) or {}).get("gates") or {}
        for key, ok in gates.items():
            if not ok:
                reasons.append({"face_hit": "人脸检出不足", "mouth_silent": "嘴没闭住",
                                "mouth_closed": "检测到张嘴"}.get(key, key))
        shake = _edge_jitter(p)
        if shake > BG_SHAKE_GATE:
            reasons.append(f"背景晃动 {shake:.2f}")
        freeze = _freeze_run(p)
        if freeze >= FREEZE_GATE:
            reasons.append(f"定格 {freeze} 帧")
        ws = face_widths(p)
        v = ws[~np.isnan(ws)]
        if len(v) >= 10 and 100 * (v.max() - v.min()) / v.mean() > SWING_GATE:
            reasons.append(f"脸宽极差 {100 * (v.max() - v.min()) / v.mean():.1f}%")
        if reasons:
            bad[p.stem] = reasons
    return bad


def base_quality_loop(name: str, stage_dir: Path, prog: Progress, segs,
                      ref_path: Path, driver_dir: Path, qa_out: Path,
                      max_rounds: int = 2) -> dict:
    """基础段质量门：**在过渡生成之前**跑（2026-11-09 用户要求）。

    动机：11-09 实跑 rawv1 时，idle1 背景晃动 0.94 不合格，但门限放在过渡
    之后——14 条过渡全做完才报出来，重掷 idle1 后又要重跑 4 条受影响过渡，
    白烧约 40 分钟。现在顺序反过来：动作段不合格就地重掷，绝不让坏段进过渡。

    流程：qa_segments（只看基础段）→ gate_base_segments → 有不合格就换 seed
    重掷 → 重新选优 + 调色 → 再检，最多 max_rounds 轮。
    """
    bad: dict[str, list[str]] = {}
    for rnd in range(1, max_rounds + 1):
        prog.write(4, f"正在检查动作段质量（第 {rnd}/{max_rounds} 轮）…")
        # check=False：qa 有不合格段时以非零码退出，但报告照常落盘——
        # 这正是触发重掷的正常路径，绝不能在这里抛异常砸产线
        run(["studio/qa_segments.py", "--segments", str(stage_dir),
             "--out", str(qa_out / "qa_report.json")], check=False)
        bad = gate_base_segments(stage_dir, qa_out / "qa_report.json")
        if not bad:
            prog.write(4, f"动作段质量全部通过（{len(segs)} 段）——可以开始生成过渡")
            return {}
        if rnd == max_rounds:
            break
        bad_segs = sorted(bad)
        new_seed = 100 + rnd * 10
        prog.write(4, f"动作段不合格（{bad}），换 seed {new_seed} 就地重掷 {bad_segs}"
                      "——重掷完才生成过渡，不浪费过渡时间")
        sub_run = f"{name}_g_h{rnd}s{new_seed}"
        sub_wf = ROOT / "runs" / "make_avatar" / name / "workflows"
        run(["studio/regen_scail.py", "submit",
             "--segments", ",".join(bad_segs), "--seeds", str(new_seed),
             "--run", sub_run, "--ref-image", str(ref_path),
             "--driver-dir", str(driver_dir),
             # 帧率显式传 + 提交后校验：重掷段与首轮段必须同帧率，否则组装后
             # 动作段之间速度不一致（2026-11-12 补——此前重掷只靠配方默认值，
             # 配方被误改时静默按 20fps 出片，到终检才发现）
             "--force-rate", "25", "--out-fps", "25",
             "--workflow-dir", str(sub_wf)])
        _verify_scail_fps(sub_wf, bad_segs, expect=25, seed=new_seed)
        # 超时按队列深度动态放大：写死 6 小时在队列被堵时会让重掷任务
        # 永远轮不到就超时（与 scail_heal 同款死循环，2026-11-20 修复）
        run(["studio/regen_scail.py", "collect", "--run", sub_run,
             "--timeout", str(_queue_wait_minutes() * 60)])
        # 重掷段强制采用新产出（不参与比选）——旧段是已知不合格的，选回去
        # 重掷就白做了；门限下一轮会重新检测这一版
        stage_select(name, prog, segs, force_new=set(bad_segs))
        run(["studio/unify_color.py", "--segments", str(stage_dir), "--apply",
                 "--scope", "base"])
    return bad


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


def _drop_queued(pids: list[str]) -> int:
    """把仍在 ComfyUI pending 队列里的旧任务删掉（重掷前腾位）。

    ComfyUI 的 /queue POST {"delete":[prompt_id]} 只删未执行项；已执行/已丢失
    的 pid 返回后自动忽略。返回删除数（仅日志用）。"""
    import urllib.request

    try:
        from generation import config
        server = config.SERVER
    except Exception:
        return 0
    try:
        q = json.load(urllib.request.urlopen(server + "/queue", timeout=30))
    except Exception:
        return 0
    pending = [it[1] for it in q.get("queue_pending", [])
               if isinstance(it, (list, tuple)) and len(it) > 1]
    to_delete = [pid for pid in pids if pid in pending]
    if not to_delete:
        return 0
    try:
        req = urllib.request.Request(
            server + "/queue",
            data=json.dumps({"delete": to_delete}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30)
        print(f"[自愈腾位] 已从 ComfyUI 队列删除旧任务 {len(to_delete)} 个：{to_delete}", flush=True)
        return len(to_delete)
    except Exception as error:
        print(f"[自愈腾位] 删除旧队列任务失败（不影响重掷）：{error}", flush=True)
        return 0


def _queue_wait_minutes() -> int:
    """按 ComfyUI 队列深度估算消化时间（分钟）：pending×60/段 + running 一段的余量。

    实测口径：新面孔首跑单段 24~60 分钟（引擎同占显存时更慢）。collect 的
    固定 6 小时窗 < 队列消化时间时，重掷任务永远轮不到就超时——死循环根因
    （2026-11-20 实锤：missing.json 全是「collect 超时」，ComfyUI 0 失败）。
    """
    import urllib.request
    try:
        from generation import config
        server = config.SERVER
        q = json.load(urllib.request.urlopen(server + "/queue", timeout=30))
        depth = len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
        return max(360, depth * 60 + 120)  # 每任务 60 分钟 + 2 小时余量，下限 6 小时
    except Exception:
        return 360  # 查不到就按原默认 6 小时


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
        # 重掷前把旧 pid 的 pending 项从 ComfyUI 队列删除：它们就是要重掷的，
        # 不删会堵在队尾，重掷任务排队时间超过 collect 窗口就死循环
        _drop_queued([m["pid"] for rid in run_ids
                      if (mp := ROOT / "runs" / rid / "missing.json").is_file()
                      for m in json.loads(mp.read_text(encoding="utf-8"))])
        for new_seed, segs in sorted(by_seed.items()):
            sub_run = f"{run_name}_h{rnd}s{new_seed}"
            run(["studio/regen_scail.py", "submit",
                 "--segments", ",".join(sorted(set(segs))), "--seeds", str(new_seed),
                 "--run", sub_run, "--ref-image", str(ref_path),
                 "--driver-dir", str(driver_dir)])
            new_runs.append(sub_run)
        heal_runs.extend(new_runs)
        wait_min = _queue_wait_minutes()
        for sub_run in new_runs:
            run(["studio/regen_scail.py", "collect", "--run", sub_run,
                 "--timeout", str(wait_min * 60)])

def _post_backend(path: str, backend_url: str, timeout: float = 30.0) -> dict | None:
    """调后端的引擎管理端点；失败返回 None（让位失败不阻断产线——最坏是
    显存争抢，比直接崩掉强）。"""
    import urllib.request

    url = backend_url.rstrip("/") + path
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            import json as _json
            return _json.loads(resp.read().decode("utf-8"))
    except Exception as error:
        print(f"[引擎让位] {path} 调用失败（不影响产线）：{error}", flush=True)
        return None


def _engine_yield(prog: "Progress", backend_url: str) -> bool:
    """停数字人引擎让出显存；返回是否真的停了（决定 finally 要不要恢复）。"""
    data = _post_backend("/dh/engine/stop", backend_url)
    stopped = bool(data and data.get("stopped"))
    if stopped:
        prog.write(1, "数字人引擎已让位（显存全给生成），制作完成后自动恢复")
        print("[引擎让位] 引擎已停止，显存释放给 SCAIL/LTX", flush=True)
    else:
        print("[引擎让位] 引擎未在运行，无需让位", flush=True)
    return stopped


def _engine_restore(prog: "Progress", backend_url: str) -> None:
    """恢复数字人引擎（finally 调用，失败只提示不抛）。"""
    data = _post_backend("/dh/engine/start", backend_url)
    if data and data.get("started"):
        prog.write(TOTAL, "制作完成，数字人引擎恢复中（约 1 分钟后可对话）", ok=True)
        print("[引擎让位] 引擎已恢复（加载约 1 分钟）", flush=True)


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
    ap.add_argument("--backend-url", default="http://127.0.0.1:8081",
                    help="后端地址（引擎让位/恢复用；制作期间停数字人引擎把显存"
                         "全给 ComfyUI，完成后自动拉起）")
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

    # finally 里要恢复引擎——必须在 try 之前定义，否则 dry-run 提前 return 时
    # finally 引用未赋值变量直接 NameError（2026-11-12）
    engine_yielded = False

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

        # 过渡对自适应：只生成两端都在本次段清单里的对（单段模式自然为空）
        pairs = [] if args.skip_transitions else [
            p for p in TRANS_PAIRS
            if all(x in segs for x in p.split(" to "))]
        pairs_planned = bool(pairs)
        qa_out = ROOT / "runs" / "make_avatar" / name

        # GPU 让位（2026-11-12 用户要求"全力跑 AI 生成"）：数字人引擎常驻
        # 7~9G 显存，与 SCAIL/LTX 争 16G 会互相拖死——引擎在线时实测
        # ComfyUI OOM、CosyVoice3 合成慢到 75s 超时。制作全程停引擎，finally
        # 里恢复；只停引擎进程，后端不重启，检测/首页全程不受影响。
        # skip_transitions 且 skip_gen（纯续跑 QA/组装，不碰 GPU）时不必停。
        gpu_work = (not skip_gen and not args.from_select) or bool(pairs)
        if gpu_work:
            engine_yielded = _engine_yield(prog, args.backend_url)

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

        # 2 选优（2026-11-09 起无后处理：模型出什么样就用什么样）
        if skip_gen:
            stage_dir = work / "segments"
            tick(2, "resume：跳过挑选，直接续跑过渡", dry_sleep=0)
        else:
            tick(2, "正在回收动作段产物…", dry_sleep=0)
            msg, stage_dir = stage_select(name, prog, segs)
            # 只量不改：把稳定性指标写进进度，供人工判断；不做任何校正/插帧/
            # 背景板/稳腿——产品由生成质量决定，不合格就重掷（见阶段 4 门限）。
            stats = _segment_stability(stage_dir)
            swings = [s[0] for s in stats.values()]
            jitters = {p.stem: _edge_jitter(p) for p in sorted(stage_dir.glob("*.mp4"))
                       if " to " not in p.stem}
            freezes = {p.stem: _freeze_run(p) for p in sorted(stage_dir.glob("*.mp4"))
                       if " to " not in p.stem}
            if not stats:
                raise RuntimeError("没有回收到任何动作段产物（SCAIL 全部失败），请重试")
            prog.write(2, f"动作段就位（{len(stats)} 段）：脸宽极差最大 "
                          f"{max(swings):.1f}%、背景晃动最大 {max(jitters.values()):.2f}、"
                          f"最长定格 {max(freezes.values())} 帧——全部为原始产物，未做任何处理")
            # 调色（2026-11-09 用户批准的唯一后处理：只改颜色，不裁不改画面内容）。
            # 必须在这里跑完：过渡的端点帧取自基础段末帧，基础段的颜色必须先定，
            # 否则过渡端点与基础段对不上，接缝出现明暗台阶（=另一种闪切）。
            # 纯调色：整帧 Reinhard LAB + 脸部常量 offset，帧数/构图/动作一律不动。
            run(["studio/unify_color.py", "--segments", str(stage_dir), "--apply",
                 "--scope", "base"])
            prog.write(2, "调色完成（基础段颜色已统一；未做任何裁切/插帧/修正）")
            tick(2, msg)

        # 2.5 动作段质量门（2026-11-09 用户要求：先查动作段，再产过渡）
        # 放在这里是刻意的——11-09 rawv1 实跑教训：门限原来放在过渡之后，
        # idle1 不合格时 14 条过渡已经全做完，重掷后还要重跑 4 条受影响过渡，
        # 白烧约 40 分钟。现在坏段就地重掷，通过才放行过渡。
        base_bad: dict[str, list[str]] = {}
        if not skip_gen and pairs_planned:
            base_bad = base_quality_loop(name, stage_dir, prog, segs,
                                         ref_path, driver_dir, qa_out)
            if base_bad:
                prog.write(3, f"动作段 2 轮重掷后仍不合格：{base_bad}"
                              "（带残差继续生成过渡，建议完成后删除重做或换照片）")

        # 3 过渡（⑦ 自愈循环 ≤3 轮：seed 801/802/803，缺什么/超标什么重掷什么；
        #    collect 软失败（missing.json）与 gate 尖峰超标都汇入 remaining。
        #    ② 配方换中性版：默认 guizi 配方的 pos 写死了男像/夜景，会把鬼子
        #    身份从过渡段拉回来）
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
            # 超时按本轮待生成对数动态放大（每对实测 ~17 分钟 + 排队余量）：
            # 写死 3 小时在全量 14 对时会提前判死，触发不必要的自愈循环
            trans_wait = max(10800, len(todo) * 40 * 60)
            run(["studio/transition_queue.py", "collect", "--run", run_id,
                 "--timeout", str(trans_wait), "--seg-dir", str(stage_dir)])
            # 2026-11-09 用户拍板：模型出什么样就用什么样——光流修瞬移、剪尾段
            # 沉降、尖峰帧混合、腿部稳定全部撤掉。过渡有问题靠终检门重掷。
            # 终检门：尖峰>10 ∪ 黑帧/亮度崩塌（collect 内硬拦）∪ 定格超限 ∪ 仍缺失
            rest = set(gate_transitions(stage_dir))
            for p in sorted(stage_dir.glob("* to *.mp4")):
                fz = _freeze_run(p)
                if fz >= FREEZE_GATE:
                    rest.add(p.stem)
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
        elif pairs:
            # 过渡统一到基础段的最终颜色（纯调色：SCAIL 与 LTX 渲染域不同，
            # 不统一的话每条过渡两端都会出现明暗台阶）。基础段已是目标色，
            # 这次只会动过渡段——transitions-only 是硬约束：基础段被二次染色
            # 曾把嘴闭门（mouth_dark 0.54→0.53）顶翻，终检白跑 14 条过渡
            # （2026-11-10 e2e 实锤）。帧数/构图/动作一律不动。
            run(["studio/unify_color.py", "--segments", str(stage_dir),
                 "--apply", "--scope", "transitions"])
            prog.write(3, "过渡调色完成（与基础段同色；未做任何裁切/插帧/修正）")
        tick(3, "过渡生成完成")

        # 4 QA（基础段门已在阶段 2.5 跑完——顺序是 2026-11-09 用户特意调整的：
        #   先查动作段再产过渡，避免坏段白烧一遍过渡时间。这里只补身份审计
        #   与过渡门汇总，基础段若在 2.5 就没过，base_bad 会带到这里如实记录）
        # 注意：qa_segments/audit_identity 的 --out 都是文件路径，绝不能共用
        # 同名前缀——曾把 "qa" 同时当文件(qa_segments)和目录(audit)用，第二轮
        # 起写入即 PermissionError（2026-11-08 实锤）。各用独立文件名。
        tick(4, "正在进行质量检查…", dry_sleep=0)
        ref = "+".join(segs)
        run(["studio/audit_identity.py", "--segments", str(stage_dir),
             "--expect", ref, "--refs", f"{ref},my_avatar,yy,guizi",
             "--out", str(qa_out / "identity.json")])
        # 兜底复检一次基础段（此时文件已定型，理论上与 2.5 结论一致）。
        # check=False：qa 非零码=有段不合格（正常结论），产线带残差继续，
        # 绝不在终检这里崩——降级上架也是上架，缺什么如实写进进度
        run(["studio/qa_segments.py", "--segments", str(stage_dir),
             "--out", str(qa_out / "qa_report.json")], check=False)
        base_bad = gate_base_segments(stage_dir, qa_out / "qa_report.json")
        if base_bad:
            prog.write(4, f"基础段仍不合格：{base_bad}（动作段门在阶段 2.5 已重掷过，"
                          "属顽固项；建议删除该人物重做或更换照片）")
        elif remaining:
            tick(4, f"质量检查完成（动作段全部通过；{len(remaining)} 条过渡未过门）")
        else:
            tick(4, "质量检查完成（全部通过）")

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
    finally:
        # 成功/失败/中断都必须把数字人引擎还回来——用户做完制作还要用陪伴页
        if engine_yielded:
            _engine_restore(prog, args.backend_url)


if __name__ == "__main__":
    main()
