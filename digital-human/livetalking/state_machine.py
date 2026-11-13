"""Shared avatar choreography and Whisper ASR helpers.

The project stores the idle, action, talking and transition clips in one
pre-rendered avatar timeline.  This module is the single source of truth for
that layout and injects the small amount of state required by ``BaseAvatar``.
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from threading import RLock, Thread

from utils.logger import logger

ROOT = Path(__file__).resolve().parent

IDLE_RANGES = [(0, 159), (160, 384), (385, 709)]
IDLE_TYPES = [1, 2, 3]
ACTION_RANGES = {
    4: (710, 859),
    5: (860, 1009),
    6: (1010, 1184),
    7: (1185, 1325),
}
ACTION_MAP = {"greet": 4, "encourage": 5, "praise": 6, "comfort": 7}
TALK_RANGE = (1326, 1550)
TALK2_RANGE = (1551, 1750)

TYPE_NAMES = {
    1: "idle1",
    2: "idle2",
    3: "idle3",
    4: "greet",
    5: "encourage",
    6: "praise",
    7: "comfort",
    8: "talk",
    9: "talk2",
    # 10 = dance：舞蹈段（dance/ 目录的视频构建而来），由页面按钮触发，
    # 不进 LLM 动作标签表 —— LLM 不该自己决定跳舞。
    10: "dance",
}
NAME_TO_TYPE = {name: state for state, name in TYPE_NAMES.items()}


def load_transition_ranges(path: str | os.PathLike[str] | None = None) -> dict[tuple[int, int], tuple[int, int]]:
    """Load and validate transition ranges from ``full_ranges.json``."""
    ranges_path = Path(path) if path else ROOT / "data" / "full_ranges.json"
    if not ranges_path.is_absolute():
        ranges_path = ROOT / ranges_path
    if not ranges_path.exists():
        logger.warning("[State] transition range file not found: %s", ranges_path)
        return {}

    try:
        raw = json.loads(ranges_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取过渡范围文件 {ranges_path}: {exc}") from exc

    result: dict[tuple[int, int], tuple[int, int]] = {}
    for name, value in raw.items():
        if name == "model" or not (
            isinstance(value, list)
            and len(value) == 2
            and all(isinstance(item, int) for item in value)
        ):
            continue
        parts = name.split("_last_to_")
        if len(parts) != 2:
            continue
        from_name, to_name = parts[0], parts[1].removesuffix("_first")
        if from_name in NAME_TO_TYPE and to_name in NAME_TO_TYPE:
            result[(NAME_TO_TYPE[from_name], NAME_TO_TYPE[to_name])] = (
                value[0],
                value[1],
            )
    return result


TRANSITION_RANGES = load_transition_ranges()

# 动作名 → 状态号（与 ACTION_MAP 反向，供段表解析用）。
# dance 只来自段表 + 页面按钮，不在 LLM 标签体系里。
NAME_STATES = {"greet": 4, "encourage": 5, "praise": 6, "comfort": 7, "dance": 10}


def load_avatar_plan(path: str | os.PathLike[str] | None) -> dict | None:
    """加载单人自定义段表（zzf_full_ranges.json 的段落式格式）。

    格式为 ``{"idle1": {"range": [0, 183]}, "idle_to_comfort": {...}, "_total": n}``，
    与 full_ranges.json 的"过渡式"格式不同。解析成 setup_state_machine 的
    plan 结构；文件缺失或格式不符返回 None（调用方回退默认时间线）。
    """
    if not path:
        return None
    plan_path = Path(path)
    if not plan_path.is_absolute():
        plan_path = ROOT / plan_path
    if not plan_path.exists():
        return None
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[State] 段表 %s 无法读取: %s", plan_path, exc)
        return None
    if not isinstance(raw, dict) or "_total" not in raw:
        return None  # 不是段落式格式

    def seg_range(name: str) -> tuple[int, int] | None:
        value = raw.get(name)
        if isinstance(value, dict) and isinstance(value.get("range"), list):
            lo, hi = value["range"][0], value["range"][1]
            if isinstance(lo, int) and isinstance(hi, int) and lo <= hi:
                return (lo, hi)
        return None

    idle_entries: list[tuple[str, tuple[int, int]]] = []
    action_ranges: dict[int, tuple[int, int]] = {}
    talk = talk2 = None

    # 第一遍：收集所有段落（idle 编号按出现顺序确定，与名字解耦）
    for name in raw:
        if name.startswith("_"):
            continue
        value = raw[name]
        if not (isinstance(value, dict) and isinstance(value.get("range"), list)):
            continue
        rng = seg_range(name)
        if rng is None:
            continue
        lowered = name.lower()
        if "_to_" in lowered:
            continue  # 第二遍处理
        elif lowered in NAME_STATES:
            action_ranges[NAME_STATES[lowered]] = rng
        elif lowered.startswith("idle"):
            idle_entries.append((name, rng))
        elif lowered == "talk":
            talk = rng
        elif lowered == "talk2":
            talk2 = rng

    if not idle_entries or talk is None:
        logger.warning("[State] 段表 %s 缺少 idle/talk 段，忽略", plan_path)
        return None
    idle_ranges = [rng for _, rng in idle_entries]
    idle_types = list(range(1, len(idle_ranges) + 1))
    idle_name_type = {
        entry_name: 1 + index for index, (entry_name, _) in enumerate(idle_entries)
    }

    def state_of(seg: str) -> int | None:
        if seg in NAME_STATES:
            return NAME_STATES[seg]
        if seg in idle_name_type:
            return idle_name_type[seg]
        if seg.startswith("idle"):
            return idle_types[0]  # 裸 "idle" 指第一个待机段
        if seg == "talk2":
            return 9
        if seg.startswith("talk"):
            return 8
        return None

    # 第二遍：过渡段 X_to_Y → (from 状态, to 状态)
    transitions: dict[tuple[int, int], tuple[int, int]] = {}
    for name in raw:
        lowered = name.lower()
        if "_to_" not in lowered:
            continue
        rng = seg_range(name)
        if rng is None:
            continue
        src_name, dst_name = lowered.split("_to_", 1)
        src_state = state_of(src_name)
        dst_state = state_of(dst_name)
        if src_state and dst_state:
            transitions[(src_state, dst_state)] = rng

    talk2 = talk2 or talk  # 没有 talk2 就复用 talk 循环

    # 过渡泛化：段表通常只写一条 idle→X / X→idle，而状态机里 idle 有多个
    # 段号、talk 有 8/9 两个。同源同向的过渡复制到兄弟状态，缺失的组合
    # 由 trigger_transition 自动降级为直切。
    # 泛化副本的起点姿势与实际所在状态不符（如 idle2→greet 借 idle1→greet
    # 的过渡，首帧姿势是 idle1 的），播放出来就是"人物瞬间挪位"。精确边
    # 单独留一份给多跳路由用（2026-11-04 ly 实测页面切换跳变的修复）。
    transitions_exact = dict(transitions)
    all_states = set(idle_types) | set(action_ranges) | {8, 9}
    for dst in all_states:
        idle_sources = [s for (s, d) in transitions if d == dst and s in idle_types]
        if idle_sources:
            base = transitions[(idle_sources[0], dst)]
            for i in idle_types:
                transitions.setdefault((i, dst), base)
        from_talk = transitions.get((8, dst))
        if from_talk and (9, dst) not in transitions:
            transitions[(9, dst)] = from_talk
    for src in all_states:
        to_talk = transitions.get((src, 8))
        if to_talk and (src, 9) not in transitions:
            transitions[(src, 9)] = to_talk
        idle_targets = [d for (s, d) in transitions if s == src and d in idle_types]
        if idle_targets:
            base = transitions[(src, idle_targets[0])]
            for j in idle_types:
                transitions.setdefault((src, j), base)
    return {
        "idle_ranges": idle_ranges,
        "idle_types": idle_types,
        "action_ranges": action_ranges,
        "talk": talk,
        "talk2": talk2,
        "transitions": transitions,
        "transitions_exact": transitions_exact,
        "source": plan_path.name,
    }


def build_transition_routes(exact: dict[tuple[int, int], tuple[int, int]]) -> dict[tuple[int, int], list[int]]:
    """在精确过渡边上做全对最短路（BFS），为缺失的直连组合生成绕行路线。

    返回 ``(from, to) -> [h1, h2, ..., to]``：到达队列（不含起点，含终点）。
    路径上每条边都是段表里的精确过渡，人物姿势因此连续——比借用兄弟
    过渡（起点姿势对不上）观感好得多。图不连通的组合不进表（调用方直切）。
    """
    adj: dict[int, list[int]] = {}
    for (src, dst) in exact:
        if src != dst:
            adj.setdefault(src, []).append(dst)
    routes: dict[tuple[int, int], list[int]] = {}
    for start in sorted(adj):
        parent: dict[int, int | None] = {start: None}
        queue = [start]
        while queue:
            cur = queue.pop(0)
            for nxt in adj.get(cur, []):
                if nxt not in parent:
                    parent[nxt] = cur
                    queue.append(nxt)
        for dst, p in parent.items():
            if dst == start or p is None:
                continue
            path = [dst]
            cur = dst
            while parent[cur] is not None:
                cur = parent[cur] or start
                path.append(cur)
            path.reverse()
            routes[(start, dst)] = path[1:]
    return routes


def init_asr(model_name: str = "small"):
    """Initialize OpenAI Whisper using the project-local model cache."""
    import whisper

    cache_dir = ROOT / "models" / "whisper"
    cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info("[ASR] loading Whisper model %s", model_name)
    return whisper.load_model(model_name, download_root=str(cache_dir))


def setup_state_machine(
    sess,
    *,
    ranges_file: str | os.PathLike[str] | None = None,
    hold_audio: bool = False,
    plan: dict | None = None,
) -> None:
    """Attach the choreography state machine to an avatar session.

    ``hold_audio`` delays speech until the idle-to-action transition has
    completed.  It must stay disabled for the normal HTTP API, where text
    should be spoken immediately.

    ``plan`` overrides the default timeline (see :func:`load_avatar_plan`);
    a plan may carry its own transition table, in which case ``ranges_file``
    is ignored.
    """
    plan = plan or {
        "idle_ranges": IDLE_RANGES,
        "idle_types": IDLE_TYPES,
        "action_ranges": ACTION_RANGES,
        "talk": TALK_RANGE,
        "talk2": TALK2_RANGE,
    }
    frame_count = int(sess.get_avatar_length())
    if frame_count <= plan["talk2"][1]:
        raise ValueError(
            f"avatar 只有 {frame_count} 帧，动作编排至少需要 {plan['talk2'][1] + 1} 帧"
        )

    if plan.get("transitions"):
        transitions = dict(plan["transitions"])
    else:
        transitions = load_transition_ranges(ranges_file)
    transitions = {
        key: value for key, value in transitions.items() if value[1] < frame_count
    }
    # 精确边（泛化副本之外的原生过渡）：多跳路由只走精确边，保证姿势连续。
    # 默认表（无 plan）本身就是精确边，直接复用。
    if plan.get("transitions_exact") is not None:
        exact_transitions = {
            key: value for key, value in plan["transitions_exact"].items()
            if value[1] < frame_count
        }
        # 诊断（2026-11-13 自循环不播排查）：确认段表边与帧数过滤结果
        logger.info(
            "[State][diag] 段表装配: avatar=%s 帧数=%d 精确边=%d 自循环边(2,2)=%s",
            getattr(getattr(sess, "opt", None), "avatar_id", "?"),
            frame_count,
            len(exact_transitions),
            exact_transitions.get((2, 2)),
        )
    else:
        exact_transitions = dict(transitions)
    routes = build_transition_routes(exact_transitions)

    idle_types = list(plan["idle_types"])
    action_ranges = dict(plan["action_ranges"])
    sess.idle_ranges = list(plan["idle_ranges"])
    sess.action_ranges = action_ranges
    sess.talk_start, sess.talk_end = plan["talk"]
    sess.talk2_start, sess.talk2_end = plan["talk2"]
    sess.transition_ranges = transitions

    sess._in_transition = False
    sess._transition_target = None
    sess._pending_route = None
    sess._talk_cycle_done = False
    sess._speech_ever_active = False
    sess._speech_started = False
    sess._speaking_now = False  # 实时说话态（complete_transition 补跳 talk 用）
    sess._pending_action = None
    sess._prefetched_target = None
    # 最近播放过的动作 {状态号: 时间戳}——说话时连续同动作去重用
    # （2026-11-12 用户实测：连说几句"你好"都带 greet 标签，人物每轮都跳去
    # greet 段再跳回来，从不安定下来说话，看起来"播放逻辑混乱"）
    sess._recent_actions: dict[int, float] = {}
    sess._current_state = random.choice(idle_types)
    sess._prev_audiotype = sess._current_state
    sess._in_talk = False
    sess._pending_route = None
    sess._hold_audio = hold_audio
    sess._audio_buffer = []

    original_set_state = sess.set_custom_state
    original_init = sess.init_customindex

    def preload_range(
        start: int,
        end: int,
        *,
        include_face: bool = False,
        background: bool = True,
    ) -> None:
        """Warm compressed avatar frames before their playback window."""
        targets = [getattr(sess, "frame_list_cycle", None)]
        if include_face:
            targets.append(getattr(sess, "face_list_cycle", None))

        def load() -> None:
            started = time.perf_counter()
            for target in targets:
                preloader = getattr(target, "preload_range", None)
                if preloader is not None:
                    preloader(start, end + 1)
            logger.debug(
                "[Frames] prefetched %d-%d%s in %.2fs",
                start,
                end,
                " with faces" if include_face else "",
                time.perf_counter() - started,
            )

        if background:
            Thread(
                target=load,
                name=f"frame-prefetch-{start}-{end}",
                daemon=True,
            ).start()
        else:
            load()

    def preload_state(state: int, *, background: bool = True) -> None:
        start, end = range_for(state)
        preload_range(
            start,
            end,
            include_face=state in action_ranges or state in (8, 9),
            background=background,
        )

    def range_for(state: int) -> tuple[int, int]:
        if state in idle_types:
            return sess.idle_ranges[state - 1]
        if state in action_ranges:
            return action_ranges[state]
        if state == 8:
            return (sess.talk_start, sess.talk_end)
        if state == 9:
            return (sess.talk2_start, sess.talk2_end)
        raise ValueError(f"未知动作状态: {state}")

    def activate(state: int, *, reinit: bool = True) -> None:
        start, end = range_for(state)
        if sess._prefetched_target == state:
            sess._prefetched_target = None
        else:
            preload_state(state)
        sess.action_start, sess.action_end = start, end
        sess.custom_audiotype = state
        sess._current_state = state
        sess._prev_audiotype = state
        sess._in_talk = state in (8, 9)
        sess._in_transition = False
        sess._transition_target = None
        sess._pending_route = None
        sess._reset_index = True
        if state in idle_types:
            sess._speech_started = False
            sess._speech_ever_active = False
        original_set_state(state, reinit)
        # BaseAvatar's legacy method ignores states without custom image cycles.
        sess.custom_audiotype = state

    def trigger_transition(from_state: int, to_state: int) -> bool:
        # 自过渡（idle2→idle2 自循环）：段表里有 (s,s) 精确边时播这段过渡，
        # 让末帧姿势经 1 秒插值演回起点；没有才原地重播（旧行为）。
        # 没有这个分支，自循环会在每轮结束硬跳一次（loop closure ~4.8）。
        self_edge = (
            exact_transitions.get((from_state, to_state))
            if from_state == to_state
            else None
        )
        if from_state == to_state and self_edge is None:
            # 诊断（2026-11-13）：自循环请求但没有精确边——记录后按旧行为重播
            logger.info(
                "[State][diag] 自循环 %s→%s 无精确边（精确边表共 %d 条），原地重播",
                TYPE_NAMES.get(from_state, from_state),
                TYPE_NAMES.get(to_state, to_state),
                len(exact_transitions),
            )
        if (
            from_state == to_state
            and self_edge is None
            and not getattr(sess, "_pending_route", None)
        ):
            activate(to_state)
            return False
        # 三级查找：精确直连 → 精确边多跳绕行（姿势连续）→ 兄弟泛化副本
        # （起点姿势可能与当前状态不符）→ 都没有则直切。
        route: list[int] = []
        transition_range = exact_transitions.get((from_state, to_state))
        if transition_range is None and not (
            from_state in idle_types and to_state in idle_types
        ):
            # idle↔idle 不走多跳（2026-11-13）：唯一的 idle 间绕行路径是
            # idle3→idle1→idle2，会让"每次从 idle3 回来都先摆一次 idle1 的
            # 姿势"——用户要求 idle1 与 idle3 解绑。缺边就交给泛化/硬切，
            # 段表注释也认为 idle 间硬切观感可接受。
            route = list(routes.get((from_state, to_state)) or [])
            if route:
                transition_range = transitions.get((from_state, route[0]))
        if transition_range is None:
            transition_range = transitions.get((from_state, to_state))
        if transition_range is None:
            # idle↔idle 缺过渡是单人段表的常态（例如 yy 只有 idle1 与
            # idle1_final 两段同姿势拷贝），硬切观感可接受，但每 7 秒刷一条
            # WARNING 会把日志淹掉——降到 debug。
            both_idle = from_state in idle_types and to_state in idle_types
            log = logger.debug if both_idle else logger.warning
            log(
                "[State] missing transition %s -> %s; switching directly",
                TYPE_NAMES.get(from_state, from_state),
                TYPE_NAMES.get(to_state, to_state),
            )
            activate(to_state)
            sess._pending_route = None
            return False
        if route:
            # 本跳只到路线上的下一个状态；complete_transition 里接力下一跳。
            # route 本身已含终点，route[1:] 即剩余站——原实现再追加一次
            # to_state 会多出重复末站，绕行收尾多烧 1~2 轮空循环
            # （2026-11-11 修复）。
            hop_target = route[0]
            sess._pending_route = list(route[1:])
            logger.info(
                "[State][route] %s → 目标 %s 途经 %s",
                TYPE_NAMES.get(from_state, from_state),
                TYPE_NAMES.get(to_state, to_state),
                [TYPE_NAMES.get(x, x) for x in route],
            )
        else:
            hop_target = to_state
            sess._pending_route = None
        # Decode the first transition frames synchronously so the playback
        # thread never has to wait on disk. 同步部分只解 4 帧（~45ms）：
        # 解 12 帧会阻塞渲染线程 135ms，等于丢 3~4 帧画面造成切换卡顿，
        # 而输出队列（≈320ms）足以覆盖后台补齐剩余帧的时间。
        preload_range(
            transition_range[0],
            min(transition_range[1], transition_range[0] + 3),
            background=False,
        )
        preload_range(*transition_range)
        sess._prefetched_target = hop_target
        preload_state(hop_target)
        sess.action_start, sess.action_end = transition_range
        sess._in_transition = True
        sess._transition_target = hop_target
        sess._in_talk = False
        sess._reset_index = True
        logger.info(
            "[State] transition %s -> %s%s [%d-%d]",
            TYPE_NAMES[from_state],
            TYPE_NAMES[hop_target],
            "（多跳绕行）" if route else "",
            transition_range[0],
            transition_range[1],
        )
        # 诊断：把帧缓存的命中率打出来。切换时命中率低、或"同步读盘累计"
        # 一直在涨，就说明预取没跟上 —— 那些帧是渲染线程自己同步读的，
        # 每帧约 11ms，而 25fps 每帧预算只有 40ms，表现就是画面卡顿。
        frames = getattr(sess, "frame_list_cycle", None)
        if frames is not None and hasattr(frames, "stats"):
            st = frames.stats()
            logger.info(
                "[Frames] 缓存 %d/%d  命中率 %.1f%%  同步读盘 %d 帧/累计 %.2fs",
                st["cached"],
                st["cache_size"],
                st["hit_rate"] * 100,
                st["misses"],
                st["miss_seconds"],
            )
        return True

    def complete_transition() -> None:
        target = sess._transition_target
        if target is None:
            sess._in_transition = False
            return
        # 语音沿在过渡中途触发时被 _in_transition 挡掉（go_talk 只在沿上调用，
        # base_avatar 侧一次性触发）——到这里补跳：先记住"该张嘴"，activate 会
        # 把 _speech_started 清掉。自循环过渡占循环 21% 时间，不补这个缺口，
        # 每五次开口就有一次画面停在待机不动嘴。
        resume_talk = target in idle_types and sess._speaking_now
        activate(target, reinit=False)
        if resume_talk:
            sess._speech_started = True
            go_talk_if_speaking()
        pending = getattr(sess, "_pending_route", None) or []
        if pending:
            # 多跳绕行：本跳已到下一站，接力触发剩余的精确过渡
            nxt = pending[0]
            sess._pending_route = pending[1:] or None
            trigger_transition(target, nxt)
            return
        sess._pending_route = None

    # idle 轮播权重（2026-11-13 用户二次定档）：主待机段 60% 自循环
    # （经 idle2→idle2 过渡回到自身），idle1/idle3 各 20% 分流。
    # 兄弟段只播一次，播完一律回主段，两者不互相绑定。
    IDLE2_WEIGHT = 0.60
    IDLE_BRANCH_PROB = 0.40

    def pick_idle(exclude: int | None = None) -> int:
        main = 2 if 2 in idle_types else (idle_types[1] if len(idle_types) > 1 else None)
        if main is None:
            return random.choice(idle_types)
        # 从兄弟段（idle1/idle3）出发：一律回主段——兄弟段只播一次，不自循环，
        # 也不互相绑定（用户 2026-11-13："这两个不能绑定"）
        if exclude is not None and exclude != main:
            return main
        # 从主段出发（exclude==main，待机轮播）或动作/说话结束（exclude=None）：
        # 主段自循环为主，偶尔分流。注意 exclude==main 时主段仍在候选里——
        # 自循环靠 idle2→idle2 过渡段演完姿势差回到起点，不是原地重播。
        pool = list(idle_types)
        others = [s for s in pool if s != main]
        if not others:
            return main
        share = IDLE_BRANCH_PROB / len(others)
        weights = [1.0 - IDLE_BRANCH_PROB if s == main else share for s in pool]
        return random.choices(pool, weights=weights, k=1)[0]

    ACTION_DEDUPE_SECONDS = 20.0

    def advance_idle(current_speaking: bool = False) -> None:
        current = sess._current_state
        pending = sess._pending_action
        sess._pending_action = None
        if pending in action_ranges:
            last = sess._recent_actions.get(pending, 0.0)
            if time.time() - last < ACTION_DEDUPE_SECONDS:
                # 该动作刚播过：不再跳过去刷存在感，直接进入说话循环
                logger.info(
                    "[State] 动作 %s 刚播过（%.0fs 内），本轮改为直接说话",
                    TYPE_NAMES.get(pending, pending),
                    time.time() - last,
                )
                pending = None
                sess._pending_action = None
                if not current_speaking:
                    target = pick_idle(current)
                    trigger_transition(current, target)
                    return
                target = 8
            else:
                sess._recent_actions[pending] = time.time()
                target = pending
        elif current_speaking:
            # 说话中空闲段播完：进入 talk 循环，而不是再切一个 idle
            target = 8
        else:
            target = pick_idle(current)
        # 诊断线：验证排队动作是否真的被消费、路由走向（2026-11-11 播放混乱排查）
        logger.info(
            "[State] advance_idle: 当前=%s 说话=%s 排队动作=%s → 目标=%s",
            TYPE_NAMES.get(current, current), "是" if current_speaking else "否",
            TYPE_NAMES.get(pending, pending) if pending else "无",
            TYPE_NAMES.get(target, target),
        )
        trigger_transition(current, target)

    def _go_talk_if_speaking_impl() -> bool:
        """Jump idle -> talk at the moment speech starts.

        Plain replies without an action tag begin speaking while an idle
        clip is on screen; without this jump the avatar would keep cycling
        idle poses with only the mouth moving. A queued action keeps
        priority: it plays first and transitions into talk afterwards.
        """
        if sess._in_transition or sess._pending_action is not None:
            return False
        if sess._current_state not in idle_types or not sess._speech_started:
            return False
        trigger_transition(sess._current_state, 8)
        return True

    def _on_cycle_complete_impl(current_speaking: bool) -> None:
        # 记录实时说话态：complete_transition 的说话补跳要靠它判断
        # "过渡演完时用户是不是正在说"（_speech_started 会被 activate 清掉）
        sess._speaking_now = current_speaking
        if sess._in_transition:
            complete_transition()
            return

        state = sess._current_state
        if state in idle_types:
            advance_idle(current_speaking)
            return

        if state in action_ranges:
            if current_speaking or sess._speech_started:
                trigger_transition(state, 8)
            else:
                trigger_transition(state, pick_idle())
            return

        if state in (8, 9):
            if current_speaking:
                trigger_transition(state, 9 if state == 8 else 8)
            else:
                sess._talk_cycle_done = True
                trigger_transition(state, pick_idle())

    # 该素材实际提供的状态段。段表里没有的动作不是错误，而是素材编排的
    # 选择（例如 yy 段表只有 comfort），调用方据此静默降级。
    supported_states = set(idle_types) | set(action_ranges) | {8, 9}

    def supports_state(state: int) -> bool:
        return state in supported_states

    def _set_state_impl(audiotype: int, reinit: bool = True):
        if audiotype == 0:
            # 语音帧不打断编排、不清排队动作（2026-11-11 实锤：说话帧一到就把
            # 排队的 greet/comfort 清掉，动作永远不登场）。只复位静音循环标记。
            sess._in_transition = False
            sess._transition_target = None
            sess._pending_route = None
            return original_set_state(0, reinit)
        if audiotype not in supported_states:
            # 明确回 False（而不是抛 ValueError）：LLM 的动作标签命中素材
            # 未编排的动作段时，应静默降级为"本条回复不播动作"。
            logger.debug(
                "[State] 段表 %s 没有状态段 %s，忽略",
                plan.get("source") or "default",
                TYPE_NAMES.get(audiotype, audiotype),
            )
            return False
        range_for(audiotype)  # validate before mutating state
        current = sess._transition_target if sess._in_transition else sess._current_state
        if current in idle_types and audiotype in action_ranges:
            # greet＝用户跟数字人打招呼后的回礼，希望尽快登场（2026-11-13 用户
            # 实测：旧逻辑一律排队，要等 idle2 播完（3.7 秒）才出场，用户听到
            # 第一句回复时画面还在待机，动作与语音完全错位）。
            #
            # 但**只能在待机完全静止时**抢跳到 greet：set_state 来自 LLM 线程，
            # 而 advance_idle 来自推理线程，两者共享 _current_state/_in_transition
            # 且无锁。原设计的 _pending_action 排队就是为了让"只有推理线程驱动
            # 状态机"——在这里直接 trigger_transition 会与推理线程正在跑的过渡
            # 抢状态（用户实测"抢播放"：画面在两段过渡之间来回跳）。
            # 因此：静止 → 立即登场；正在过渡 → 排队，等这一跳走完再登场。
            if audiotype == ACTION_MAP["greet"]:
                last = sess._recent_actions.get(audiotype, 0.0)
                if time.time() - last < ACTION_DEDUPE_SECONDS:
                    logger.info(
                        "[State] greet 刚播过（%.0fs 内），本轮跳过",
                        time.time() - last,
                    )
                    return None
                if sess._in_transition or sess._pending_action is not None:
                    sess._pending_action = audiotype
                    logger.info("[State] greet 暂存（正在过渡/已有排队），本跳完成后登场")
                    return None
                sess._recent_actions[audiotype] = time.time()
                logger.info("[State] greet 立即登场（待机静止，不等 idle 播完）")
                trigger_transition(current, audiotype)
                return None
            sess._pending_action = audiotype
            logger.info("[State] queued action %s", TYPE_NAMES[audiotype])
            return None
        if current == audiotype:
            activate(audiotype, reinit=reinit)
            return None
        trigger_transition(current, audiotype)
        return None

    def initialize() -> None:
        original_init()
        start, end = range_for(sess._current_state)
        initial_count = max(
            8,
            int(
                getattr(
                    getattr(sess, "opt", None),
                    "initial_preload_frames",
                    32,
                )
            ),
        )
        initial_end = min(end, start + initial_count - 1)
        # Only the first visible second is required before WebRTC can send its
        # first frame.  Warm the remainder concurrently while that segment is
        # playing, instead of blocking every new browser session on the whole
        # idle clip.
        preload_range(start, initial_end, background=False)
        if initial_end < end:
            preload_range(initial_end + 1, end, background=True)
        sess._prefetched_target = sess._current_state
        activate(sess._current_state, reinit=False)
        logger.info(
            "[State] initialized %s with %d transitions",
            TYPE_NAMES[sess._current_state],
            len(transitions),
        )

    # ── 状态机串行化（2026-11-13 用户实测"互相抢"）─────────────────────────
    # 三个入口分别来自不同线程：set_state 来自 LLM 线程（动作标签），
    # on_cycle_complete 来自推理线程（帧循环跑完），go_talk_if_speaking 也来自
    # 推理线程。它们共享 _current_state/_in_transition/_transition_target 且
    # 原先无锁——LLM 线程的"greet 立即登场"可能与推理线程正在跑的 advance_idle
    # 交叉，日志实锤同一时刻出现两条 route/transition 记录，画面在两段之间跳。
    # RLock 可重入：on_cycle_complete → advance_idle → trigger_transition 走同一把锁。
    # 持锁时间约 45ms（只有首 4 帧同步解码，其余预载都是后台线程），
    # 折合推理线程最多等 1 帧，可接受。
    _state_lock = RLock()

    def set_state(audiotype: int, reinit: bool = True):
        with _state_lock:
            return _set_state_impl(audiotype, reinit)

    def on_cycle_complete(current_speaking: bool = False) -> None:
        with _state_lock:
            return _on_cycle_complete_impl(current_speaking)

    def go_talk_if_speaking() -> bool:
        with _state_lock:
            return _go_talk_if_speaking_impl()

    sess.set_custom_state = set_state
    sess.init_customindex = initialize
    sess._supports_state = supports_state
    sess._activate_state = activate
    # 供 /api/dance 这类"点按钮立即表演"的入口用：先同步预加载帧
    # （页面此时显示加载动画），再 activate 切换画面。
    sess._preload_state = preload_state
    sess._trigger_transition = trigger_transition
    sess._complete_transition = complete_transition
    sess._advance_idle = advance_idle
    sess._go_talk_if_speaking = go_talk_if_speaking
    sess._on_state_cycle_complete = on_cycle_complete
