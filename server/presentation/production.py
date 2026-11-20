# -*-coding: utf-8 -*-
"""制作任务与现场录音 HTTP 适配：明确意图启动执行，进度与结果归任务。

任务开始时绑定创建账号；进度与结果按任务标识呈现，产物写入所属资源
集合并经对象观察流到达使用方。录音使用期归录音界面，停止提交把音频
交给制作能力，任务生存期归任务。
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from server.envelope import Envelope, ErrorResponse, success

PRODUCTION_ERRORS = {
    400: {"model": ErrorResponse, "description": "请求参数不合法"},
    401: {"model": ErrorResponse, "description": "未登录或登录凭证无效"},
    404: {"model": ErrorResponse, "description": "任务或录音不存在"},
}


class TaskRegistry:
    """进程内任务登记：状态归任务，登记只为进度查询入口。"""

    def __init__(self):
        self._tasks: dict[str, object] = {}

    def submit(self, task) -> str:
        task_id = uuid.uuid4().hex
        self._tasks[task_id] = task
        return task_id

    def get(self, task_id):
        return self._tasks.get(task_id)


def create_production_router(current_principal, productions: dict, recording_port) -> APIRouter:
    """productions: kind → 提交入口 ``start(owner_id, input) -> task``。"""
    registry = TaskRegistry()
    router = APIRouter(tags=["制作与录音"])

    @router.post("/production/{kind}",
                 response_model=Envelope[dict],
                 responses={code: PRODUCTION_ERRORS[code] for code in (400, 401, 404)})
    async def start_production(kind: str, payload: dict, user=Depends(current_principal)):
        start = productions.get(kind)
        if start is None:
            raise HTTPException(status_code=404, detail="制作类型不存在")
        task = await start(user.user_id, payload)
        return success({"taskId": registry.submit(task)}, "制作任务已提交")

    @router.get("/production/tasks/{task_id}",
                response_model=Envelope[dict],
                responses={code: PRODUCTION_ERRORS[code] for code in (401, 404)})
    async def production_status(task_id: str, user=Depends(current_principal)):
        task = registry.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return success(task.status())

    @router.post("/production/tasks/{task_id}/cancel",
                 response_model=Envelope[None],
                 responses={code: PRODUCTION_ERRORS[code] for code in (401, 404)})
    async def cancel_production(task_id: str, user=Depends(current_principal)):
        task = registry.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        await task.cancel()
        return success(message="任务已取消")

    # --- 现场录音 ------------------------------------------------------------
    @router.post("/recording/start", response_model=Envelope[None],
                 responses={code: PRODUCTION_ERRORS[code] for code in (400, 401)})
    async def start_recording(user=Depends(current_principal)):
        recording_port.start(user.user_id)
        return success(message="录音已开始")

    @router.post("/recording/stop-submit", response_model=Envelope[dict],
                 responses={code: PRODUCTION_ERRORS[code] for code in (400, 401, 404)})
    async def stop_and_submit_recording(user=Depends(current_principal)):
        result = recording_port.stop_and_submit(user.user_id)
        if result is None:
            raise HTTPException(status_code=400, detail="当前没有进行中的录音")
        task_id = registry.submit(result["task"])
        return success({"taskId": task_id}, "录音已提交，克隆任务开始")

    @router.post("/recording/cancel", response_model=Envelope[None],
                 responses={code: PRODUCTION_ERRORS[code] for code in (400, 401)})
    async def cancel_recording(user=Depends(current_principal)):
        if not recording_port.cancel(user.user_id):
            raise HTTPException(status_code=400, detail="当前没有进行中的录音")
        return success(message="录音已取消")

    return router


class AccountRecordingPort:
    """按账号管理未提交录音的使用期；submit 由装配绑定到制作任务。"""

    def __init__(self, recording_factory):
        self._recording_factory = recording_factory
        self._by_account: dict[int, object] = {}

    def _of(self, owner_id):
        recording = self._by_account.get(owner_id)
        if recording is None:
            recording = self._recording_factory(owner_id)
            self._by_account[owner_id] = recording
        return recording

    def start(self, owner_id):
        recording = self._of(owner_id)
        recording.set_usage(True)
        recording.start()

    def stop_and_submit(self, owner_id):
        recording = self._by_account.get(owner_id)
        if recording is None:
            return None
        submitted = recording.stop_and_submit()
        recording.set_usage(False)
        return {"task": submitted}

    def cancel(self, owner_id):
        recording = self._by_account.get(owner_id)
        if recording is None:
            return False
        recording.cancel()
        recording.set_usage(False)
        return True


__all__ = ["AccountRecordingPort", "TaskRegistry", "create_production_router"]
