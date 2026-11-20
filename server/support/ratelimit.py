"""按客户端地址的滑动窗口请求限流；预览签发与设备信令共用。"""

import time
from collections import deque


class SlidingWindowRateLimiter:
    """每个键保留窗口内的事件时间戳：达到上限即拒绝，不排队不过期补偿。"""

    def __init__(self, max_events: int, window_seconds: float):
        self._max_events = max_events
        self._window = window_seconds
        self._events: dict[str, deque] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        events = self._events.setdefault(key, deque())
        while events and now - events[0] > self._window:
            events.popleft()
        if len(events) >= self._max_events:
            return False
        events.append(now)
        return True
