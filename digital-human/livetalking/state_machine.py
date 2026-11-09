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
from threading import Thread

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
    sess._pending_action = None
    sess._prefetched_target = None
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
        if from_state == to_state and not getattr(sess, "_pending_route", None):
            activate(to_state)
            return False
        # 三级查找：精确直连 → 精确边多跳绕行（姿势连续）→ 兄弟泛化副本
        # （起点姿势可能与当前状态不符）→ 都没有则直切。
        route: list[int] = []
        transition_range = exact_transitions.get((from_state, to_state))
        if transition_range is None:
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
            # 本跳只到路线上的下一个状态；complete_transition 里接力下一跳
            hop_target = route[0]
            sess._pending_route = list(route[1:]) + [to_state]
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
        activate(target, reinit=False)
        pending = getattr(sess, "_pending_route", None) or []
        if pending:
            # 多跳绕行：本跳已到下一站，接力触发剩余的精确过渡
            nxt = pending[0]
            sess._pending_route = pending[1:] or None
            trigger_transition(target, nxt)
            return
        sess._pending_route = None

    def pick_idle(exclude: int | None = None) -> int:
        choices = [state for state in idle_types if state != exclude]
        return random.choice(choices or idle_types)

    def advance_idle(current_speaking: bool = False) -> None:
        current = sess._current_state
        pending = sess._pending_action
        sess._pending_action = None
        if pending in action_ranges:
            target = pending
        elif current_speaking:
            # 说话中空闲段播完：进入 talk 循环，而不是再切一个 idle
            target = 8
        else:
            target = pick_idle(current)
        trigger_transition(current, target)

    def go_talk_if_speaking() -> bool:
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

    def on_cycle_complete(current_speaking: bool) -> None:
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

    def set_state(audiotype: int, reinit: bool = True):
        if audiotype == 0:
            sess._pending_action = None
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
