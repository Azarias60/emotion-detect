# -*- coding: utf-8 -*-
"""talk→过渡→talk 镜像双帧端点链条（v2 方案）。

v1 教训：首=尾=同一帧 F + 双端 guide 0.85 → 全段静止（ratio 0.23/0.96）。
v2 设计（2026-10-14 标定轮实链验证）：
  F1=底段帧38（最稳窗中点）、F2=帧32（稳定点，|F1-F2|=6.53）
  talk 段 = F1→F2（81 帧）、过渡段 = F2→F1（57 帧，镜像端点）
  强化嘴动 pos（talks to the camera 开头）——talk ratio 1.93/~5 开合周期、
  过渡 ratio 2.70/1 完整周期、三段接缝 2.17/1.91、四段循环 wrap 1.91。
  端点钉死 38/32：重算只作校验（不符即报错，不自动改选——复用标定资产的前提）。

阶段（断点续跑，状态在 runs/talk_chain_review/<run>/state.json）：
  stage base    ①SCAIL 底段（优先复用 v1/talkbase.mp4）②F1/F2 校验+上传
                 ③标定成片入缓存（v2calibD/T -> talk_s801/trans_s811，幂等）
  stage calib   ④标定（缓存优先，不重跑 LTX）→ 嘴/躯干比判档（1.3/1.0）
  stage batch   ⑤ratio≥1.3 才批量：talk 802/803 + 过渡 812/813（811 已有缓存），
                 单帧尖峰选优
  stage finish  ⑥拼接 talk→过渡→talk（单次播放版）+ D→T→D→T（循环播放版，实测 wrap）
                 ⑦metrics.json ⑧QA_master ⑨双目录落盘
                 （接缝 >5 时按 color_match 预案处理后重拼）

用法（项目根目录，python = E:\\ZCodeWorkspace\\venv\\Scripts\\python.exe -u）：
  python -m pipeline.make_talk --run v2 stage base
  python -m pipeline.make_talk --run v2 stage calib
  python -m pipeline.make_talk --run v2 stage batch
  python -m pipeline.make_talk --run v2 stage finish
"""
import argparse
import json
import os
import shutil
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

import config
from comfy import api
from graphs import ltx_flf, scail
from pipeline.make_chain import concat, load_recipes

NAG = "no subtitles, text overlays, garbled text, unknown language"  # ltx 配方 neg_nag
# v2 pos（与 2026-10-14 标定提交逐字节一致——ComfyUI /history 核验，
#   证据落盘 scratch/emotionDetect/talk_chain/calib_{D,T}_submitted_pos.json）
POS_TALK = ("The character talks to the camera, his mouth opening wide and closing "
            "fully in a clear repeating rhythm, lips parting and pressing together "
            "again and again at a steady even pace through the whole shot. Each "
            "opening lasts only a moment before his lips close again. He stands "
            "facing the camera in a calm neutral pose, feet planted firmly on the "
            "ground, legs straight, shoulders level, arms hanging relaxed, head up, "
            "eyes gently open. His body and head stay nearly still in the same "
            "position from start to finish, his clothing and appearance stay exactly "
            "the same as in the reference frames. The background stays exactly the "
            "same as in the first and last frames, with no change to any object, "
            "color or brightness. The camera is locked and static at a constant "
            "distance, and the brightness and color stay identical throughout.")
POS_TRANS = ("He keeps talking to the camera, his mouth opening wide and closing "
             "fully in the same clear repeating rhythm, lips parting and pressing "
             "together again and again at a steady even pace with no pause and no "
             "change in speed. Each opening lasts only a moment before his lips "
             "close again. He stands facing the camera in a calm neutral pose, feet "
             "planted firmly on the ground, legs straight, shoulders level, arms "
             "hanging relaxed, head up, eyes gently open. His body and head stay "
             "nearly still in the same position from start to finish, his clothing "
             "and appearance stay exactly the same as in the reference frames. The "
             "background stays exactly the same as in the first and last frames, "
             "with no change to any object, color or brightness. The camera is "
             "locked and static at a constant distance, and the brightness and "
             "color stay identical throughout.")
NEG = ("blurry, flickering, motion blur, distorted face, deformed hands, extra limbs, "
       "morphing, warping, unstable, jump cut, scene change, breathing, chest rising, "
       "shoulders moving, shrugging, blinking, eye closing, walking, stepping, turning "
       "around, leaning, torso rotation, arm raising, arm movement, gesture, waving, "
       "pointing, hand movement, head turning, head tilting, nodding, weight shifting, "
       "hip movement, leg movement, foot movement, sudden movement, jerk, twitch, jolt, "
       "fast motion, brightness change, color shift, frozen completely, static image, "
       "speeding up, slowing down")  # v28 neg 删除禁嘴五词
DESKTOP_ROOT = os.path.join(os.path.expanduser("~"), "Desktop", "动作替换验证", "talk过渡验证")


def free_vram():
    """POST /free 卸载模型回收显存（跑 LTX-22B Q4 前，vram 实测曾被 torch 占 ~11GB）。"""
    req = urllib.request.Request(config.SERVER + "/free",
                                 data=json.dumps({"unload_models": True,
                                                  "free_memory": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=120)
        st = json.load(urllib.request.urlopen(config.SERVER + "/system_stats", timeout=30))
        dev = st["devices"][0]
        print(f"VRAM 释放: free={dev['vram_free'] / 1e9:.2f}GB "
              f"torch={dev['torch_vram_total'] / 1e9:.2f}GB", flush=True)
    except Exception as e:
        print("VRAM 释放失败(忽略):", e, flush=True)


def seg_metrics(fr, mouth_roi, torso_roi):
    d = api.frame_diffs(fr)
    sec = [abs(d[i + 1] - d[i]) for i in range(len(d) - 1)]
    mouth = api.roi_mean_diff(fr, *mouth_roi)
    torso = api.roi_mean_diff(fr, *torso_roi)
    return {"frames": len(fr), "spike": max(d) if d else 0.0,
            "mean": float(np.mean(d)) if d else 0.0,
            "secondDiff": max(sec) if sec else 0.0,
            "flicker": abs(float(np.mean(d[1::2])) - float(np.mean(d[0::2])))
            if len(d) >= 4 else 0.0,
            "mouth": mouth, "torso": torso, "ratio": mouth / max(torso, 1e-6)}


def mouth_strip(fr, roi, out_png, n=10):
    """嘴部 ROI 特写拼图（人工判定嘴动用）。"""
    y0, y1, x0, x1 = roi
    Y0, Y1 = max(0, y0 - 30), min(fr[0].shape[0], y1 + 30)
    X0, X1 = max(0, x0 - 60), min(fr[0].shape[1], x1 + 60)
    idx = [int(i * (len(fr) - 1) / (n - 1)) for i in range(n)]
    row = np.hstack([cv2.resize(fr[i][Y0:Y1, X0:X1], (160, 120)) for i in idx])
    for j, i in enumerate(idx):
        cv2.putText(row, f"f{i}", (j * 160 + 4, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    api.imwrite_u(out_png, row)
    return out_png


def scan_mouth_roi(F, default=(350, 470, 260, 440), tol=40):  # pragma: no cover
    """[已废弃 v2] 眼线下方暗带扫描——v1 实测扫到的是眼线（darkBand 250-302），
    嘴在其下方。保留仅为对照，stage_base 已不再调用；正确 ROI 见 --mouth-roi 默认值。"""
    g = api.gray(F)
    prof = g[250:560, 280:420].mean(axis=1)  # 面部中央列带行均值
    sm = np.convolve(prof, np.ones(15) / 15, mode="same")
    lo, hi = 50, 270  # 对应 y 300..520（眼线以下、下巴以上）
    yc = lo + int(np.argmin(sm[lo:hi]))
    thr = sm[yc] + 8
    y0b, y1b = yc, yc
    while y0b - 1 >= 0 and sm[y0b - 1] < thr:
        y0b -= 1
    while y1b + 1 < len(sm) and sm[y1b + 1] < thr:
        y1b += 1
    center = (y0b + y1b) / 2 + 250
    dev = abs(center - (default[0] + default[1]) / 2)
    roi = [int(max(250, center - 60)), int(min(560, center + 60)), default[2], default[3]]
    if dev <= tol:
        roi = list(default)
    return roi, float(center), float(dev), (y0b + 250, y1b + 250)


def run_ltx(prefix, first, last, pos, frames, seed, guide):
    p = ltx_flf.build(first, last, prefix, pos=pos, neg=NEG, nag=NAG, frames=frames,
                      seed=seed, guide=guide, fps=config.FPS, width=config.W,
                      height=config.H)
    pid = api.submit(p)
    print(f"LTX {prefix} -> {pid}", flush=True)
    if not pid or not api.wait(pid, prefix, timeout=2700):
        print(f"[FAIL] {prefix} 生成失败", flush=True)
        return None
    src = os.path.join(config.COMFY_OUTPUT, prefix + "_00001.mp4")
    fr = api.frames_of(src, min_frames=frames // 2)
    if len(fr) < frames // 2:
        print(f"[FAIL] {prefix} 读帧失败（{len(fr)} 帧）", flush=True)
        return None
    return src, fr


def load_state(run_dir):
    p = os.path.join(run_dir, "state.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def save_state(run_dir, st):
    with open(os.path.join(run_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- stages ----
def stage_base(a, run_dir, st):
    free_vram()
    base_mp4 = os.path.join(run_dir, "talkbase.mp4")
    # v1→v2 底段复用（漏拷会重跑 SCAIL 且钉死帧号 38/32 失效）
    v1_base = os.path.join(config.RUNS_DIR, "talk_chain_review", "v1", "talkbase.mp4")
    if not os.path.exists(base_mp4) and os.path.exists(v1_base) and not a.force:
        shutil.copy(v1_base, base_mp4)
        print(f"复用 v1 底段 -> {base_mp4}", flush=True)
    if os.path.exists(base_mp4) and not a.force:
        fr = api.frames_of(base_mp4)
        print(f"复用底段 {base_mp4}（{len(fr)} 帧）", flush=True)
    else:
        _, sc_recipe = load_recipes(a.scail_recipe)
        pose_name = api.ensure_input_file(a.pose)
        cap = api.video_info(os.path.join(config.COMFY_INPUT, pose_name)
                             if not os.path.exists(a.pose) else a.pose)[0]
        ref = api.ensure_input_file(a.ref_image)
        prefix = f"scail2/{a.run}_talkbase"
        p = scail.build(a.scail_base, pose_video=pose_name, cap=cap, prefix=prefix,
                        prompt=sc_recipe["prompt"],
                        negative=sc_recipe.get("negative", ""),
                        force_rate=sc_recipe.get("force_rate", 20),
                        fps=sc_recipe.get("fps", 20), ref_image=ref)
        pid = api.submit(p)
        print(f"SCAIL 底段（{pose_name} {cap}帧@{sc_recipe.get('force_rate', 20)}fps "
              f"ref={ref}）-> {pid}", flush=True)
        if not pid or not api.wait(pid, "SCAIL talkbase", timeout=2700):
            sys.exit("[FAIL] SCAIL 底段生成失败")
        out = scail.output_path(prefix)
        fr = api.frames_of(out)
        if len(fr) < 10:
            sys.exit(f"[FAIL] {out} 读帧失败（{len(fr)} 帧）")
        shutil.copy(out, base_mp4)
        print(f"SCAIL 底段完成: {out}（{len(fr)} 帧）-> {base_mp4}", flush=True)

    # ② 端点帧 v2：钉死 F1=38（最稳 11 帧窗中点）/ F2=32（稳定点，|F1-F2|=6.53）。
    #    重算只作校验——不符即报错退出，禁止自动改选（否则与已标定资产脱锚）。
    d = api.frame_diffs(fr)
    win = 10
    sums = [sum(d[i:i + win]) for i in range(len(d) - win + 1)]
    best_i = int(np.argmin(sums))
    F1_IDX, F2_IDX = 38, 32
    if best_i + 5 != F1_IDX:
        sys.exit(f"[FAIL] 最稳窗中点={best_i + 5} != 钉定 F1={F1_IDX}——"
                 f"底段与标定不符（换姿态视频需重新标定，不许沿用 38/32）")
    F1, F2 = fr[F1_IDX], fr[F2_IDX]
    df = float(np.abs(api.gray(F1) - api.gray(F2)).mean())
    if not (4.0 <= df <= 9.0):
        sys.exit(f"[FAIL] |F1-F2|={df:.2f} 不在标定带 [4,9]（标定值 6.53）")
    f1_png = os.path.join(run_dir, f"ep_F1_idx{F1_IDX}.png")
    f2_png = os.path.join(run_dir, f"ep_F2_idx{F2_IDX}.png")
    api.imwrite_u(f1_png, F1)
    api.imwrite_u(f2_png, F2)
    n1, n2 = api.upload_image(f1_png), api.upload_image(f2_png)
    st.update({"talkbase": base_mp4, "baseFrames": len(fr),
               "F1_index": F1_IDX, "F2_index": F2_IDX, "F1F2diff": df,
               "F1_input": n1, "F2_input": n2,
               "windowSumsMin": float(sums[best_i])})
    print(f"端点帧: F1=底段第{F1_IDX}帧 F2=底段第{F2_IDX}帧 |F1-F2|={df:.2f} "
          f"-> input:{n1} / {n2}", flush=True)

    # ②' 标定成片入缓存（幂等）：talk s801 / trans s811 不重跑 LTX
    for src_name, cache_name in [("v2calibD_s801_00001.mp4", "talk_s801.mp4"),
                                 ("v2calibT_s811_00001.mp4", "trans_s811.mp4")]:
        cache = os.path.join(run_dir, cache_name)
        src = os.path.join(config.COMFY_OUTPUT, src_name)
        if os.path.exists(cache):
            continue
        if os.path.exists(src):
            shutil.copy(src, cache)
            print(f"标定资产入缓存: {src_name} -> {cache_name}", flush=True)
        else:
            print(f"[WARN] 未找到标定成片 {src_name}，{cache_name} "
                  f"将由后续 stage 重新生成（提示词一致，标定外推降级为同分布新采样）",
                  flush=True)

    # ③ 嘴 ROI：v2 固定值（F 帧裁剪视觉 x303-367/y358-378 + 暗带交叉定位）。
    #    禁用扫描：scan_mouth_roi 找最暗带会命中眼线（v1 实测 darkBand 250-302 是眼线）。
    st["mouthRoi"] = list(a.mouth_roi)
    st["mouthRoiSource"] = "v2 固定值（标定轮实测定位，扫描已禁用）"
    st["bodyRoi"] = list(a.torso_roi)
    chk = cv2.resize(F1, (352, 640))
    y0, y1, x0, x1 = [v // 2 for v in st["mouthRoi"]]
    cv2.rectangle(chk, (x0, y0), (x1, y1), (0, 255, 255), 2)
    cv2.putText(chk, f"mouthROI {st['mouthRoi']}", (6, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    cv2.rectangle(chk, (a.torso_roi[2] // 2, a.torso_roi[0] // 2),
                  (a.torso_roi[3] // 2, a.torso_roi[1] // 2), (0, 0, 255), 2)
    api.imwrite_u(os.path.join(run_dir, "F_roi_check.png"), chk)
    save_state(run_dir, st)
    print(f"嘴ROI={st['mouthRoi']}（{st['mouthRoiSource']}）躯干ROI={st['bodyRoi']}",
          flush=True)
    print("stage base 完成", flush=True)


def stage_calib(a, run_dir, st):
    if "F1_input" not in st:
        sys.exit("[FAIL] 先跑 stage base")
    cache = os.path.join(run_dir, f"talk_s{a.talk_seeds[0]}.mp4")
    if os.path.exists(cache) and not a.force:
        fr = api.frames_of(cache, min_frames=a.talk_frames // 2)
        if len(fr) >= a.talk_frames // 2:
            print(f"标定复用缓存 {cache}（{len(fr)} 帧，不重跑 LTX）", flush=True)
        else:
            fr = None
    else:
        fr = None
    if fr is None:
        free_vram()
        r = run_ltx(f"ltx23_{a.run}_talk_s{a.talk_seeds[0]}", st["F1_input"],
                    st["F2_input"], POS_TALK, a.talk_frames, a.talk_seeds[0], a.guide)
        if not r:
            sys.exit("[FAIL] 标定段生成失败")
        src, fr = r
        shutil.copy(src, cache)
    m = seg_metrics(fr, st["mouthRoi"], st["bodyRoi"])
    st["calib"] = {"seed": a.talk_seeds[0], "src": cache, **{k: v for k, v in m.items()}}
    mouth_strip(fr, st["mouthRoi"], os.path.join(run_dir, f"QA_mouth_calib_s{a.talk_seeds[0]}.png"))
    save_state(run_dir, st)
    print(f"标定 s{a.talk_seeds[0]}: 帧数={m['frames']} 尖峰={m['spike']:.2f} "
          f"均={m['mean']:.2f} 二阶差分max={m['secondDiff']:.2f} 闪烁={m['flicker']:.2f} "
          f"嘴={m['mouth']:.2f} 躯干={m['torso']:.2f} ratio={m['ratio']:.2f}", flush=True)
    r_ = m["ratio"]
    if r_ >= 1.3:
        print(f"标定结论: ratio={r_:.2f} ≥1.3（本链 LTX 冻结底线 0.84、标定真嘴动 "
              f"1.69~2.70）→ 允许批量（stage batch）", flush=True)
    elif r_ >= 1.0:
        print(f"标定结论: ratio={r_:.2f} 在 [1.0,1.3) 人工复核带 → 看 "
              f"QA_mouth_calib_s{a.talk_seeds[0]}.png 嘴部特写再决定是否批量", flush=True)
    else:
        print(f"标定结论: ratio={r_:.2f} <1.0（低于冻嘴参照 0.84+余量）→ 嘴冻死，"
              f"不批量（方案：重标定或换 F2）", flush=True)
    print("stage calib 完成", flush=True)


def _gen_and_measure(a, run_dir, st, kind, seed):
    frames = a.talk_frames if kind == "talk" else a.trans_frames
    pos = POS_TALK if kind == "talk" else POS_TRANS
    prefix = f"ltx23_{a.run}_{kind}_s{seed}"
    cache = os.path.join(run_dir, f"{kind}_s{seed}.mp4")
    if os.path.exists(cache) and not a.force:
        fr = api.frames_of(cache)
        if len(fr) >= frames // 2:
            m = seg_metrics(fr, st["mouthRoi"], st["bodyRoi"])
            print(f"复用 {cache}（{len(fr)} 帧）", flush=True)
            return {"seed": seed, "src": cache, **m}
    # v2 镜像端点：talk F1→F2，过渡 F2→F1
    first = st["F1_input"] if kind == "talk" else st["F2_input"]
    last = st["F2_input"] if kind == "talk" else st["F1_input"]
    r = run_ltx(prefix, first, last, pos, frames, seed, a.guide)
    if not r:
        return None
    src, fr = r
    m = seg_metrics(fr, st["mouthRoi"], st["bodyRoi"])
    shutil.copy(src, cache)
    mouth_strip(fr, st["mouthRoi"], os.path.join(run_dir, f"QA_mouth_{kind}_s{seed}.png"))
    print(f"{kind} s{seed}: 帧数={m['frames']} 尖峰={m['spike']:.2f} 二阶差分max="
          f"{m['secondDiff']:.2f} 嘴={m['mouth']:.2f} 躯干={m['torso']:.2f} "
          f"ratio={m['ratio']:.2f}", flush=True)
    return {"seed": seed, "src": cache, **m}


def stage_batch(a, run_dir, st):
    if "F1_input" not in st:
        sys.exit("[FAIL] 先跑 stage base")
    calib_ratio = st.get("calib", {}).get("ratio", 0.0)
    print(f"批量前置检查: 标定 ratio={calib_ratio:.2f}", flush=True)
    free_vram()
    st.setdefault("talk", {})
    st.setdefault("trans", {})
    # talk：标定段(801) 已有则直接入表，其余 seed 生成
    if st.get("calib"):
        st["talk"][str(st["calib"]["seed"])] = {k: v for k, v in st["calib"].items()}
    for seed in a.talk_seeds:
        if str(seed) in st["talk"]:
            continue
        r = _gen_and_measure(a, run_dir, st, "talk", seed)
        if r:
            st["talk"][str(seed)] = r
            save_state(run_dir, st)
    for seed in a.trans_seeds:
        if str(seed) in st["trans"]:
            continue
        r = _gen_and_measure(a, run_dir, st, "trans", seed)
        if r:
            st["trans"][str(seed)] = r
            save_state(run_dir, st)
    if not st["talk"] or not st["trans"]:
        sys.exit("[FAIL] talk 或过渡段全部 seed 失败")

    def pick(d):
        ok = [v for v in d.values() if v["ratio"] >= 1.3]
        pool, why = (ok, "ratio>=1.3 中尖峰最小") if ok else \
            (list(d.values()), "无 seed 达 ratio 1.3，退化为尖峰最小（ratio 门将 FAIL）")
        best = min(pool, key=lambda v: v["spike"])
        return best, why

    bt, why_t = pick(st["talk"])
    btr, why_r = pick(st["trans"])
    st["pick"] = {"talk": {"seed": bt["seed"], "src": bt["src"], "why": why_t},
                  "trans": {"seed": btr["seed"], "src": btr["src"], "why": why_r}}
    save_state(run_dir, st)
    print(f"talk 选优: s{bt['seed']}（尖峰 {bt['spike']:.2f}，{why_t}）", flush=True)
    print(f"过渡选优: s{btr['seed']}（尖峰 {btr['spike']:.2f}，{why_r}）", flush=True)
    print("stage batch 完成", flush=True)


def stage_finish(a, run_dir, st):
    if "pick" not in st:
        sys.exit("[FAIL] 先跑 stage batch")
    talk_src, trans_src = st["pick"]["talk"]["src"], st["pick"]["trans"]["src"]
    ft = api.frames_of(talk_src)
    ftr = api.frames_of(trans_src)
    # 接缝预案（方案 b）：双接缝任一 >5 时对过渡段做 color_match（两侧同传 talk 帧）
    def seam_of(fc):
        dc = api.frame_diffs(fc)
        return dc[len(ft) - 1], dc[len(ft) + len(ftr) - 1]
    concat_path = os.path.join(run_dir, f"完整衔接_{a.run}.mp4")
    concat([talk_src, trans_src, talk_src], concat_path)
    fc = api.frames_of(concat_path)
    sa, sb = seam_of(fc)
    print(f"首拼 {len(fc)} 帧: seamA={sa:.2f} seamB={sb:.2f}", flush=True)
    # v2 循环版交付：talk→过渡→talk→过渡（止于 F1 锚，wrap 回 talk 首帧 F1 锚）
    # 三段版 wrap 实测 7.09 超门（末帧锚 F2 vs 首帧锚 F1），循环播放必须用四段版
    loop_path = os.path.join(run_dir, f"完整衔接_{a.run}_循环.mp4")
    concat([talk_src, trans_src, talk_src, trans_src], loop_path)
    fl = api.frames_of(loop_path)
    loop_wrap = float(np.abs(api.gray(fl[0]) - api.gray(fl[-1])).mean())
    print(f"循环版 {len(fl)} 帧: wrap(末帧vs首帧)={loop_wrap:.2f}"
          f"（三段版对照 7.09；内接缝同级为过）", flush=True)
    if sa > 5 or sb > 5:
        print("接缝 >5，启用 color_match_transition 预案（两侧传同一段 talk 帧）",
              flush=True)
        ft_c, cm = api.color_match_transition(ft, ftr, ft, run_dir, tag=f"{a.run}_trans")
        if cm:
            trans_src = cm
            ftr = ft_c
            concat([talk_src, trans_src, talk_src], concat_path)
            fc = api.frames_of(concat_path)
            sa, sb = seam_of(fc)
            print(f"色匹配后重拼: seamA={sa:.2f} seamB={sb:.2f}", flush=True)
        else:
            print("[WARN] color_match 失败，保留首拼结果（指标门将 FAIL）", flush=True)

    mt, mtr = seg_metrics(ft, st["mouthRoi"], st["bodyRoi"]), \
        seg_metrics(ftr, st["mouthRoi"], st["bodyRoi"])
    dc = api.frame_diffs(fc)
    metrics = {
        "framesTalk": mt["frames"], "framesTrans": mtr["frames"],
        "talkSpike": mt["spike"], "talkMean": mt["mean"],
        "transSpike": mtr["spike"], "transMean": mtr["mean"],
        "seamMotionA": sa, "seamMotionB": sb,
        "flickerDeltaTalk": mt["flicker"],
        "mouthMotion": mt["mouth"], "bodyMotion": mt["torso"],
        "mouthRoi": st["mouthRoi"], "bodyRoi": st["bodyRoi"],
        "talkSecondDiff": mt["secondDiff"], "transSecondDiff": mtr["secondDiff"],
        "transMouthMotion": mtr["mouth"], "transBodyMotion": mtr["torso"],
        "talkMouthRatio": mt["ratio"], "transMouthRatio": mtr["ratio"],
        "mouthMotionSource": "talk 段（选优 talk 成片）",
        "seeds": {"talk": st["pick"]["talk"]["seed"], "trans": st["pick"]["trans"]["seed"]},
        "concatFrames": len(fc),
        "loopWrap": loop_wrap, "loopFrames": len(fl),
    }
    with open(os.path.join(run_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # QA_master：talk / 过渡 / 全片 三条抽帧条 + 接缝四联 + talk 嘴部特写条
    def strip(fr, name):
        n = len(fr)
        idx = [0, n // 4, n // 2, 3 * n // 4, n - 1]
        row = np.hstack([cv2.resize(fr[i], (210, 382)) for i in idx])
        cv2.putText(row, name, (6, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        return row
    seam = np.hstack([cv2.resize(x, (210, 382))
                      for x in [ft[-1], ftr[0], ftr[-1], ft[0]]])
    for j, lab in enumerate(["talk last", "trans first", "trans last", "talk first"]):
        cv2.putText(seam, lab, (j * 210 + 6, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
    y0, y1, x0, x1 = st["mouthRoi"]
    n = len(ft)
    idx = [int(i * (n - 1) / 9) for i in range(10)]
    mrow = np.hstack([cv2.resize(ft[i][max(0, y0 - 30):y1 + 30,
                                  max(0, x0 - 60):x1 + 60], (160, 120))
                      for i in idx])
    cv2.putText(mrow, f"TALK MOUTH ROI f0..f{n - 1} (motion check)", (6, 116),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    api.imwrite_u(os.path.join(run_dir, "QA_master.png"),
                  np.vstack([strip(ft, "TALK (seg1, seed %s)" % st["pick"]["talk"]["seed"]),
                             strip(ftr, "TRANSITION (seg2, seed %s)" % st["pick"]["trans"]["seed"]),
                             strip(fc, "FULL CHAIN talk->trans->talk"),
                             cv2.resize(seam, (1050, 382)),
                             cv2.resize(mrow, (1050, 120))]))

    # 双目录落盘（三段版=单次播放交付；四段循环版=段库/LiveTalking 循环播放交付）
    files = {f"{a.run}_1_talk.mp4": talk_src,
             f"{a.run}_2_过渡.mp4": trans_src,
             f"{a.run}_3_talk.mp4": talk_src,
             f"完整衔接_{a.run}.mp4": concat_path,
             f"完整衔接_{a.run}_循环.mp4": loop_path}
    desktop_dir = os.path.join(DESKTOP_ROOT, a.run)
    os.makedirs(desktop_dir, exist_ok=True)
    for name, src in files.items():
        dst_r = os.path.join(run_dir, name)
        if os.path.abspath(src) != os.path.abspath(dst_r):  # 拼接产物已在 run_dir
            shutil.copy(src, dst_r)
        shutil.copy(src, os.path.join(desktop_dir, name))
    for extra in ["QA_master.png", "metrics.json"]:
        shutil.copy(os.path.join(run_dir, extra), os.path.join(desktop_dir, extra))
    if a.plan_file and os.path.exists(a.plan_file):
        for d in (run_dir, desktop_dir):
            shutil.copy(a.plan_file, os.path.join(d, "plan.json"))
    if os.path.exists(os.path.join(config.PROJECT_ROOT, "tools", "metrics_check.py")):
        for d in (run_dir, desktop_dir):
            shutil.copy(os.path.join(config.PROJECT_ROOT, "tools", "metrics_check.py"),
                        os.path.join(d, "metrics_check.py"))
    save_state(run_dir, st)
    print(f"指标: talkSpike={metrics['talkSpike']:.2f} transSpike={metrics['transSpike']:.2f} "
          f"seamA={sa:.2f} seamB={sb:.2f} flicker={metrics['flickerDeltaTalk']:.2f} "
          f"talkRatio={metrics['talkMouthRatio']:.2f} transRatio={metrics['transMouthRatio']:.2f}",
          flush=True)
    print(f"交付: {run_dir} 与 {desktop_dir}", flush=True)
    print("stage finish 完成", flush=True)


def main():
    ap = argparse.ArgumentParser(description="talk→过渡→talk 同帧闭环链条")
    ap.add_argument("--run", default="v1")
    ap.add_argument("stage", choices=["base", "calib", "batch", "finish"])
    ap.add_argument("--pose", default=r"E:\Comfyui\talk.mp4")
    ap.add_argument("--ref-image", default=r"E:\Comfyui\可爱3D人物页面导览.png")
    ap.add_argument("--scail-recipe", default="scail_3d_antijitter.json")
    ap.add_argument("--scail-base", default="greet", choices=["greet", "idle"])
    ap.add_argument("--talk-frames", type=int, default=81)
    ap.add_argument("--trans-frames", type=int, default=57)
    ap.add_argument("--guide", type=float, default=0.85)
    ap.add_argument("--talk-seeds", type=int, nargs="+", default=[801, 802, 803])
    ap.add_argument("--trans-seeds", type=int, nargs="+", default=[811, 812, 813])
    ap.add_argument("--mouth-roi", type=int, nargs=4, default=[340, 400, 280, 400],
                    help="v2 固定嘴 ROI（F 帧实测 x303-367/y358-378），一般不动")
    ap.add_argument("--torso-roi", type=int, nargs=4, default=[700, 1020, 190, 500])
    ap.add_argument("--plan-file",
                    default=r"E:\ZCodeWorkspace\scratch\emotionDetect\talk_chain\plan.json")
    ap.add_argument("--force", action="store_true", help="忽略本地缓存重新生成")
    a = ap.parse_args()

    run_dir = os.path.join(config.RUNS_DIR, "talk_chain_review", a.run)
    os.makedirs(run_dir, exist_ok=True)
    st = load_state(run_dir)
    print(f"=== {a.run} stage {a.stage} ===", flush=True)
    {"base": stage_base, "calib": stage_calib,
     "batch": stage_batch, "finish": stage_finish}[a.stage](a, run_dir, st)


if __name__ == "__main__":
    main()
