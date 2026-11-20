"""可控依赖条件；不实现任何被测业务。"""
import asyncio


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


class Signal:
    def __init__(self, value):
        self.value = value
        self.listeners = set()

    def current(self):
        return self.value

    def observe(self, listener):
        self.listeners.add(listener)
        listener(self.value)
        return lambda: self.listeners.discard(listener)

    def publish(self, value):
        self.value = value
        for listener in tuple(self.listeners):
            listener(value)


class Clock:
    def __init__(self):
        self.seconds = 0
        self.waiters = []

    def now(self):
        return self.seconds

    async def sleep(self, seconds):
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.seconds + seconds, future))
        await future

    async def advance(self, seconds):
        await settle()
        self.seconds += seconds
        for deadline, future in tuple(self.waiters):
            if deadline <= self.seconds and not future.done():
                future.set_result(None)
        self.waiters = [(t, f) for t, f in self.waiters if not f.done()]
        await settle()
