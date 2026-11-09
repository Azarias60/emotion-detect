# -*- coding: utf-8 -*-
"""LTX 2.5 int8 过渡提交器（14 条 1 秒过渡），带冒烟测试。

用法:
    python submit_ltx.py [ComfyUI地址] [--smoke]

--smoke: 先只提交 1 条并等它成功，再批量（2.5 工作流未在本机验证过，
         34GB 权重跑不动 16G 卡——租卡机先冒烟再全量，防止 14 个全废）。
产出: <ComfyUI>/output/ 下 gzt_e2egreet01_t_*2*_s801_*.mp4
"""
import json
import sys
import time
from pathlib import Path

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") \
    else "http://127.0.0.1:8188"
SMOKE = "--smoke" in sys.argv
HERE = Path(__file__).resolve().parent
WF_DIR = HERE / "workflows"
EP_DIR = HERE / "endpoints"

S = requests.Session()


def upload(path: Path):
    with open(path, "rb") as f:
        r = S.post(f"{BASE}/upload/image", files={"image": (path.name, f)},
                   data={"overwrite": "true", "type": "input"}, timeout=600)
    r.raise_for_status()


def submit(wf: Path):
    graph = json.loads(wf.read_text(encoding="utf-8"))
    r = S.post(f"{BASE}/prompt", json={"prompt": graph, "client_id": "rental"})
    r.raise_for_status()
    return r.json()["prompt_id"]


def wait_one(name, pid, timeout=3600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(10)
        try:
            e = S.get(f"{BASE}/history/{pid}", timeout=30).json().get(pid)
        except Exception:
            continue
        if not e:
            continue
        st = e.get("status") or {}
        if st.get("status_str") == "error":
            return False, "error"
        if st.get("completed"):
            files = [i.get("filename") for o in (e.get("outputs") or {}).values()
                     for k in ("videos", "gifs", "images")
                     for i in (o.get(k) or []) if isinstance(i, dict)]
            return True, files
    return False, "timeout"


def main():
    print(f"ComfyUI: {BASE}（{'冒烟模式' if SMOKE else '全量 14 条'}）")
    for f in sorted(EP_DIR.glob("*.png")):
        upload(f)
    print(f"已上传 {len(list(EP_DIR.glob('*.png')))} 张端点图")

    wfs = sorted(WF_DIR.glob("*.json"))
    if SMOKE:
        wfs = wfs[:1]
        print(f"冒烟：只提交 {wfs[0].name}")

    jobs = []
    for wf in wfs:
        pid = submit(wf)
        jobs.append((wf.stem, pid))
        print(f"提交 {wf.stem} -> {pid}")
        time.sleep(1)

    if SMOKE:
        name, pid = jobs[0]
        print(f"\n等冒烟任务 {name}…（2.5 int8 单条约 5~15 分钟）")
        ok, info = wait_one(name, pid)
        print(f"冒烟结果: {'✓ 成功' if ok else '✗ 失败'} {info}")
        if not ok:
            print("冒烟失败——先别批量。排查方向：2.5 VAE/节点兼容性、显存不足。")
            print("确认修好后去掉 --smoke 重跑，或退回 LTX 2.3 Q4（联系 ZCode 换 jobs_ltx）。")
            sys.exit(1)
        print("冒烟通过。重新跑 python submit_ltx.py（不带 --smoke）提交剩余 13 条。")
        return

    print(f"\n已提交 {len(jobs)} 条，等待完成…")
    done = 0
    pending = dict(jobs)
    deadline = time.time() + 6 * 3600
    while pending and time.time() < deadline:
        time.sleep(20)
        for name, pid in list(pending.items()):
            try:
                e = S.get(f"{BASE}/history/{pid}", timeout=30).json().get(pid)
            except Exception:
                continue
            if not e:
                continue
            st = e.get("status") or {}
            if st.get("status_str") == "error":
                print(f"  {name}: 失败")
                del pending[name]
            elif st.get("completed"):
                print(f"  {name}: ✓")
                done += 1
                del pending[name]
        if pending:
            print(f"  剩余 {len(pending)}/{len(jobs)}…")
    print(f"\n完成 {done}/{len(jobs)}。产出在 ComfyUI/output/ 下 gzt_e2egreet01_t_*2*_s801*.mp4")


if __name__ == "__main__":
    main()
