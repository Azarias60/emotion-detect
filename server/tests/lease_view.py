class LeaseView:
    """测试用准入视图：围绕 TaskAdmission 的全局容量计数提供 acquire/release。

    ``key`` 参数随全局容量语义退役——准入不分操作键，视图也不区分。
    """

    def __init__(self, admission):
        self.target = admission
        self._entered = False

    def acquire(self, blocking=False):
        with self.target._lock:
            if self.target._active >= self.target.capacity:
                return False
            self.target._active += 1
            self._entered = True
            return True

    def release(self):
        if self._entered:
            with self.target._lock:
                self.target._active -= 1
            self._entered = False

    def locked(self):
        return self.target._active > 0
