"""已提交的制作任务：执行、取消与产物交付归任务自身。

页面和登录不是已提交任务的输入；任务开始时绑定创建账号与输入，
产物只交付给所属输出集合。明确取消停止执行，不产生交付。
"""
import asyncio


class ProductionTask:
    def __init__(self, owner_id, kind, input, produce, output):
        self.owner_id = owner_id
        self.kind = kind
        self.input = input
        self._produce = produce
        self._output = output
        self._task: asyncio.Task | None = None

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def wait(self):
        if self._task is None:
            raise RuntimeError("任务尚未开始")
        await self._task

    async def cancel(self):
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def status(self):
        """任务进度事实：运行、取消、失败与完成各归其状态。"""
        if self._task is None:
            return {"status": "running", "failure": ""}
        if self._task.cancelled():
            return {"status": "cancelled", "failure": ""}
        if self._task.done():
            error = self._task.exception()
            if error is not None:
                return {"status": "failed", "failure": str(error)}
            return {"status": "complete", "failure": ""}
        return {"status": "running", "failure": ""}

    async def _run(self):
        product = await self._produce(self.input)
        # 只有真实产出交付给所属集合；取消不产生交付。
        self._output.create(self.owner_id, product)


def create_task(owner_id, kind, input, produce, output):
    """装配一个已提交任务；produce 是注入的执行端口，output 是产物集合。"""
    return ProductionTask(owner_id, kind, input, produce, output)
