# -*- coding: utf-8 -*-
"""播放逻辑体检：枚举状态机所有可能的状态转移，逐条判定通畅度。

判定分级：
  OK-精确   段表里有这条边，起点姿势天然匹配
  泛化-同源 借用同源（同一 from）兄弟段的过渡，起点姿势一致，观感 OK
  泛化-借帧 借用**其他 from** 的过渡（如 idle3→talk 借 idle1→talk），
            首帧姿势与本段不符 → 人物瞬移/跳变
  硬切      完全没有过渡段，直切（姿势突变）
  循环       talk↔talk2 的说话循环（talk_to_talk 段）

用法：
    python studio/check_playback_paths.py --avatar e2etest01
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "livetalking"
sys.path.insert(0, str(ENGINE))

NAME_STATES = {"greet": 4, "encourage": 5, "praise": 6, "comfort": 7, "dance": 10}
TYPE_NAMES = {
    1: "idle1", 2: "idle2", 3: "idle3",
    4: "greet", 5: "encourage", 6: "praise", 7: "comfort",
    8: "talk", 9: "talk2", 10: "dance",
}


def load_plan(avatar_id: str) -> dict:
    """复刻 load_avatar_plan 的解析（含泛化副本），返回精确/泛化两套边。"""
    path = ENGINE / "data" / "avatars" / avatar_id / "ranges.json"
    raw = json.loads(path.read_text(encoding="utf-8"))

    idle_entries, action_ranges = [], {}
    talk = talk2 = None
    for name, value in raw.items():
        if name.startswith("_") or not (isinstance(value, dict) and "range" in value):
            continue
        low = name.lower()
        if "_to_" in low:
            continue
        if low in NAME_STATES:
            action_ranges[NAME_STATES[low]] = value["range"]
        elif low.startswith("idle"):
            idle_entries.append((name, value["range"]))
        elif low == "talk":
            talk = value["range"]
        elif low == "talk2":
            talk2 = value["range"]

    idle_ranges = [r for _, r in idle_entries]
    idle_types = list(range(1, len(idle_ranges) + 1))
    name_type = {n: 1 + i for i, (n, _) in enumerate(idle_entries)}

    def state_of(seg: str):
        if seg in NAME_STATES:
            return NAME_STATES[seg]
        if seg in name_type:
            return name_type[seg]
        if seg.startswith("idle"):
            return idle_types[0]
        if seg == "talk2":
            return 9
        if seg.startswith("talk"):
            return 8
        return None

    exact = {}
    for name, value in raw.items():
        if "_to_" not in name.lower() or not (isinstance(value, dict) and "range" in value):
            continue
        src, dst = name.lower().split("_to_", 1)
        s, d = state_of(src), state_of(dst)
        if s and d:
            exact[(s, d)] = value["range"]

    # 复刻 state_machine 的泛化规则
    gen = dict(exact)
    all_states = set(idle_types) | set(action_ranges) | {8, 9}
    origin = {k: k for k in exact}  # 泛化边 → 实际借用的精确边
    for dst in all_states:
        idle_sources = [s for (s, d) in exact if d == dst and s in idle_types]
        if idle_sources:
            base_key = (idle_sources[0], dst)
            for i in idle_types:
                if (i, dst) not in gen:
                    gen[(i, dst)] = exact[base_key]
                    origin[(i, dst)] = base_key
    for dst in all_states:
        if (8, dst) in exact and (9, dst) not in gen:
            gen[(9, dst)] = exact[(8, dst)]
            origin[(9, dst)] = (8, dst)
    for src in all_states:
        if (src, 8) in exact and (src, 9) not in gen:
            gen[(src, 9)] = exact[(src, 8)]
            origin[(src, 9)] = (src, 8)
        idle_targets = [d for (s, d) in exact if s == src and d in idle_types]
        if idle_targets:
            for j in idle_types:
                if (src, j) not in gen:
                    gen[(src, j)] = exact[(src, idle_targets[0])]
                    origin[(src, j)] = (src, idle_targets[0])
    return {
        "idle_types": idle_types,
        "action_ranges": action_ranges,
        "exact": exact,
        "gen": gen,
        "origin": origin,
        "has_talk2": talk2 is not None and talk2 != talk,
    }


def verdict(plan: dict, src: int, dst: int) -> tuple[str, str]:
    if src == dst:
        return "循环", "同段循环"
    exact = plan["exact"]
    gen = plan["gen"]
    origin = plan["origin"]
    if (src, dst) in exact:
        return "OK-精确", "段表原生边"
    if (src, dst) in gen:
        base = origin.get((src, dst), (src, dst))
        if base[0] == src:
            return "泛化-同源", f"借用 {TYPE_NAMES.get(base[0])}→{TYPE_NAMES.get(base[1])}"
        return "泛化-借帧", f"借用 {TYPE_NAMES.get(base[0])}→{TYPE_NAMES.get(base[1])}（起点姿势不符）"
    return "硬切", "无过渡段，直切"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--avatar", default="e2etest01")
    args = ap.parse_args()
    plan = load_plan(args.avatar)
    idles = plan["idle_types"]
    actions = sorted(plan["action_ranges"])
    talks = [8, 9] if plan["has_talk2"] else [8]

    # 引擎实际会触发的转移（依据 state_machine / microphone_stream 的分支，
    # 且与 pick_idle 的现行规则一致：idle2 是中转站，其余 idle 出发只回 idle2）
    main = 2 if 2 in idles else idles[0]
    cases: list[tuple[int, int, str]] = []
    for i in idles:                      # 待机轮播（pick_idle 的真实产出）
        if i == main:
            for j in idles:
                if j != main:
                    cases.append((i, j, "待机轮播"))
        else:
            cases.append((i, main, "待机轮播"))
    for i in idles:                      # 说话开场
        cases.append((i, 8, "开口说话"))
    for i in idles:                      # 打招呼
        if 4 in actions:
            cases.append((i, 4, "打招呼"))
    for t in talks:                      # 说话循环 / 说完回待机（pick_idle(8/9) → idle2）
        other = 9 if t == 8 else 8
        if len(talks) > 1:
            cases.append((t, other, "说话循环"))
        cases.append((t, main, "说完回待机"))
    for t in talks:                      # 说话中触发动作
        for a in actions:
            cases.append((t, a, "说话中动作"))
    for a in actions:                    # 动作结束：说话中回 talk，否则按权重回 idle
        cases.append((a, 8, "动作后说话"))
        for i in idles:
            cases.append((a, i, "动作后待机"))

    seen = set()
    rows = []
    for src, dst, why in cases:
        if (src, dst, why) in seen:
            continue
        seen.add((src, dst, why))
        mark, detail = verdict(plan, src, dst)
        rows.append((why, TYPE_NAMES.get(src, src), TYPE_NAMES.get(dst, dst), mark, detail))

    order = {"硬切": 0, "泛化-借帧": 1, "泛化-同源": 2, "OK-精确": 3, "循环": 4}
    rows.sort(key=lambda r: (order.get(r[3], 9), r[0]))

    print(f"=== {args.avatar} 播放路径体检 ===")
    print(f"待机段 {len(idles)} 个 / 动作段 {[TYPE_NAMES[a] for a in actions]} / talk 段 {len(talks)}\n")
    width = max(len(r[0]) for r in rows) + 2
    print(f"{'场景':<{width}}{'转移':<22}{'判定':<12}说明")
    print("-" * 100)
    bad = 0
    for why, s, d, mark, detail in rows:
        flag = "" if mark in ("OK-精确", "泛化-同源", "循环") else "  ←"
        print(f"{why:<{width}}{s + ' → ' + d:<22}{mark:<12}{detail}{flag}")
        if mark in ("硬切", "泛化-借帧"):
            bad += 1
    print("-" * 100)
    print(f"有问题的转移：{bad} / {len(rows)}")


if __name__ == "__main__":
    main()
