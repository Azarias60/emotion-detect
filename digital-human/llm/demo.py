# -*- coding: utf-8 -*-
"""命令行演示 / 自测：不依赖前端，直接验证 LLM 对话包全链路。

用法：
    python demo.py                 # 交互对话
    python demo.py --once "你好"    # 单轮测试
    python demo.py --selftest       # 本地自测（不打 API，只验证解析/切句/记忆）
"""

import argparse
import sys

from .action_parser import ActionTagParser
from .config import LLM_CONFIG, is_local_endpoint
from .dialogue import stream_dialogue
from .llm_memory import ConversationMemory
from .text_slicer import SegmentSlicer


def selftest() -> int:
    """离线自测：标签剥离、模块识别、切句、记忆。"""
    ok = True

    # 1) 动作标签剥离
    p = ActionTagParser()
    body = "".join(p.feed(c) for c in "[greet]你好呀，今天感觉怎么样？")
    if p.action != "greet" or not body.startswith("你好呀"):
        print(f"[FAIL] 动作标签: action={p.action} body={body!r}")
        ok = False
    else:
        print(f"[ok] 动作标签剥离: action=greet body={body!r}")

    # 2) 模块标签识别（另起一行）
    p2 = ActionTagParser()
    body2 = "".join(p2.feed(c) for c in "听起来你累坏了，要注意休息。\n[MODULE:fatigue]\n")
    if p2.module != "fatigue":
        print(f"[FAIL] 模块标签: module={p2.module}")
        ok = False
    else:
        print(f"[ok] 模块标签识别: module=fatigue")

    # 3) 无标签直放（不增加延迟）
    p3 = ActionTagParser()
    body3 = "".join(p3.feed(c) for c in "你好")
    if p3.action is not None or body3 != "你好":
        print(f"[FAIL] 无标签: action={p3.action} body={body3!r}")
        ok = False
    else:
        print(f"[ok] 无标签直放: body={body3!r}")

    # 4) 切句：强标点 8 字即切，弱标点要攒够；流结束 flush 放行剩余
    s = SegmentSlicer(8, 24, 32, 12, 36, 60, first_weak_min=8, weak_min=14)
    segs = s.feed("你好呀，今天感觉怎么样呢？我陪你聊聊。")
    segs += s.flush()
    if len(segs) < 2 or not segs[0].endswith("？"):
        print(f"[FAIL] 切句: {segs}")
        ok = False
    else:
        print(f"[ok] 切句: {segs}")

    # 5) 记忆：标签不进历史
    m = ConversationMemory(max_turns=2)
    m.append("user", "你好")
    m.append("assistant", "[greet]你好呀")
    msgs = m.messages()
    if any("[" in c["content"] for c in msgs):
        print(f"[FAIL] 记忆含标签: {msgs}")
        ok = False
    else:
        print(f"[ok] 记忆清洗: {msgs}")

    print("\n自测" + ("全部通过" if ok else "有失败"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="数字人 LLM 对话包演示")
    ap.add_argument("--once", type=str, help="单轮测试")
    ap.add_argument("--selftest", action="store_true", help="离线自测")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    print(f"LLM 端点: {LLM_CONFIG['base_url']}  模型: {LLM_CONFIG['model']}  "
          f"本地端点: {is_local_endpoint(LLM_CONFIG['base_url'])}")
    memory = ConversationMemory()

    def one(message: str) -> None:
        print(f"\n你: {message}")
        action = None
        module = None
        full = ""
        for ev in stream_dialogue(message, memory=memory):
            if ev.type == "action":
                action = ev.text
                print(f"  [动作] {action} -> 数字人开演")
            elif ev.type == "segment":
                print(f"  [语音段] {ev.text}")
            elif ev.type == "module":
                module = ev.text
            elif ev.type == "done":
                full = ev.text
            elif ev.type == "error":
                print(f"  [错误] {ev.text}")
        print(f"小C: {full}")
        if module:
            print(f"  [模块推进] {module} -> 语音提示 + 用户确认后跳转")

    if args.once:
        one(args.once)
        return 0

    print("输入quit退出")
    while True:
        try:
            line = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in ("quit", "exit", "q"):
            break
        if line:
            one(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
