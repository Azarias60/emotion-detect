"""导览与陪伴的会话上下文：存在条件、唤醒与重置归各自使用期。

导览上下文随首页使用期建立与结束；陪伴上下文在同一次登录内保留，
离页只暂停消费。唤醒（"你好"）进入交流，重复问候不清空已有对话；
三分钟无交流回到待命。模型 Key 在每一轮发送时读取当前账号配置，
上下文不因配置变化重置。
"""
import asyncio

from server import settings

# 唤醒词与无交流重置窗口沿用共同语音规则；文字发送可直接交互。
WAKE_WORDS = ("你好",)
IDLE_RESET_SECONDS = float(getattr(settings, "CONVERSATION_IDLE_RESET_SECONDS", 180))


class _Context:
    def __init__(self, owner_id, login_id, kind, engine, model_config):
        self.owner_id = owner_id
        self.kind = kind
        self.engine = engine
        self.model_config = model_config
        self.login_id = login_id
        self.usage = False
        self.resource = None
        self.awake = False
        self.idle_timer: asyncio.TimerHandle | None = None

    def _open(self):
        if self.resource is None:
            self.resource = self.engine.open()
        return self.resource

    def _close(self):
        resource, self.resource = self.resource, None
        if resource is not None:
            self._standby()
            resource.close()

    def _standby(self):
        self.awake = False
        if self.idle_timer is not None:
            self.idle_timer.cancel()
            self.idle_timer = None

    def _arm_idle_reset(self):
        if self.idle_timer is not None:
            self.idle_timer.cancel()
        loop = asyncio.get_running_loop()
        self.idle_timer = loop.call_later(IDLE_RESET_SECONDS, self._standby)


def create_context(owner_id, login_id, kind, engine, model_config):
    """装配一份会话上下文；engine 与 model_config 是注入的依赖条件。"""
    state = _Context(owner_id, login_id, kind, engine, model_config)

    class Context:
        async def set_usage(self, usage):
            """页面使用期：导览离页结束上下文，陪伴同次登录内保留。"""
            state.usage = usage
            if usage:
                state._open()
            elif kind == "guide":
                state._close()

        async def set_login(self, login_id):
            """登录期变化：本次登录结束即结束上下文，不跨登录继承。"""
            if state.login_id is not None and login_id != state.login_id:
                state._close()
            state.login_id = login_id

        async def restart(self):
            """显式重新开始：清空本次对话，回到已唤醒可直接交流的状态。"""
            if state.resource is not None:
                state.resource.clear()
            state.awake = True
            state._arm_idle_reset()

        async def send(self, text):
            """文字发送直接交互；下一轮读取当前账号的模型配置。"""
            if not state.usage:
                return None
            key = state.model_config.read(state.owner_id)
            reply = await state._open().reply(text, key)
            state._arm_idle_reset()
            return reply

        async def hear(self, text):
            """语音输入：待命中只有唤醒被消费，交流中的语音按轮处理。"""
            if not state.usage:
                return None
            if not state.awake:
                if any(wake in text for wake in WAKE_WORDS):
                    state.awake = True
                    state._arm_idle_reset()
                return None
            if any(wake in text for wake in WAKE_WORDS):
                # 重复问候只保持交流状态，不清空已有对话。
                state._arm_idle_reset()
                return None
            key = state.model_config.read(state.owner_id)
            reply = await state._open().reply(text, key)
            state._arm_idle_reset()
            return reply

    return Context()
