"""System clock adapter for expression observation timestamps."""

import time


class SystemClock:
    def now(self) -> float:
        return time.time()
