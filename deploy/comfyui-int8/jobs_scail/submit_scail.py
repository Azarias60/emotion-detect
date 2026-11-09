# -*- coding: utf-8 -*-
"""SCAIL int8 提交器（14 个基础段任务），支持单条冒烟。

用法:
    python submit_scail.py [ComfyUI地址]            # 上传素材 + 提交全部 14 个 + 等完成
    python submit_scail.py [地址] --only idle1_s1    # 只提交/等待单条（冒烟用）
    python submit_scail.py [地址] --no-wait          # 只提交不等待（批量后台跑）
产出: <ComfyUI>/output/scail2/e2egreet01_g_*_00001_.mp4
"""
import json
import sys
import time
from pathlib import Path

import requests

args = sys.argv[1:]
BASE = next((a for a in args if not a.startswith("--")), "http://127.0.0.1:8188")
ONLY = next((a for i, a in enumerate(args)
             if a == "--only" and i + 1 < len(args)), "")
NO_WAIT = "--no-wait" in args

HERE = Path(__file__).resolve().parent
WF_DIR = HERE / "workflows"
INPUTS = HERE / "inputs"

S = requests.Session()


def upload(path: Path):
    with open(path, "rb") as f:
        r = S.post(f"{BASE}/upload/image",
                   files={"image": (path.name, f)},
                   data={"overwrite": "true", "type": "input"}, timeout=600)
    r.raise_for_status()
    print(f"  上传 {path.name} ({path.stat().st_size / 1048576:.0f} MB)")


def wait_one(name, pid, timeout=7200):
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(15)
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
    mode = f"单条 {ONLY}" if ONLY else "全部 14 条"
    print(f"ComfyUI: {BASE}（{mode}）")
    for f in sorted(INPUTS.glob("*.*")):
        upload(f)
    for f in sorted((INPUTS / "drivers").glob("*.mp4")):
        upload(f)
    print("素材上传完成")

    wfs = sorted(WF_DIR.glob("*.json"))
    if ONLY:
        wfs = [w for w in wfs if ONLY in w.stem]
        if not wfs:
            sys.exit(f"没有匹配 {ONLY} 的工作流")
    jobs = []
    for wf in wfs:
        graph = json.loads(wf.read_text(encoding="utf-8"))
        r = S.post(f"{BASE}/prompt", json={"prompt": graph, "client_id": "rental"})
        r.raise_for_status()
        pid = r.json()["prompt_id"]
        jobs.append((wf.stem, pid))
        print(f"提交 {wf.stem} -> {pid}")
        time.sleep(1)

    if NO_WAIT:
        print(f"\n已提交 {len(jobs)} 个（不等待）。轮询: python {Path(__file__).name} --poll "
              + " ".join(p for _, p in jobs))
        return

    if ONLY:
        name, pid = jobs[0]
        print(f"\n等 {name}…（int8 单条 5~25 分钟，看 ComfyUI 日志进度）")
        ok, info = wait_one(name, pid)
        print(f"结果: {'✓ 成功' if ok else '✗ 失败'} {info}")
        if not ok:
            print("冒烟失败——先排查再批量。看 comfyui.log 尾部找原因。")
            sys.exit(1)
        print("冒烟通过。跑批量: python submit_scail.py --no-wait")
        return

    print(f"\n已提交 {len(jobs)} 个任务，等待完成…")
    done = 0
    pending = dict(jobs)
    deadline = time.time() + 8 * 3600
    while pending and time.time() < deadline:
        time.sleep(30)
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
    print(f"\n完成 {done}/{len(jobs)}。产出在 ComfyUI/output/scail2/")


if __name__ == "__main__":
    main()
