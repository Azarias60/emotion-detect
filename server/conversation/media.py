"""媒体消费：连接、聆听与播放的使用期归媒体使用，不拥有上下文或录音。

有效聆听依赖用户聆听意图与录音状态：录音期间暂停聆听与播报，录音
结束按原意图恢复；有效资源变化停止当前播报并更新媒体输入；配置缺失
结束媒体使用，不自动改选其他对象。
"""


class _MediaState:
    def __init__(self, transport, recording, resources):
        self.transport = transport
        self.recording = recording
        self.resources = resources
        self.usage = False
        self.listening_intent = False
        self.applied_resources = None
        self.stops = []

    def _effective_listening(self):
        return self.usage and self.listening_intent and not self.recording.current()

    def _on_recording(self):
        if self.recording.current():
            # 录音期间暂停聆听与播报；录音结束按原聆听意图恢复。
            self.transport.listen(False)
            self.transport.stop_playback()
        else:
            self.transport.listen(self._effective_listening())

    def _on_resources(self):
        current = self.resources.current()
        if current.get("configuration") is None:
            self._release()
            return
        if current != self.applied_resources:
            # 有效资源变化：停止当前播报并更新媒体输入。
            self.transport.stop_playback()
            self.transport.set_resources(current)
            self.applied_resources = current
        self.transport.listen(self._effective_listening())

    def _release(self):
        self.transport.listen(False)
        self.transport.stop_playback()
        self.transport.close()


def create_media_usage(transport, recording, resources):
    """装配媒体使用；transport 提供连接与播放操作，条件来自注入的信号。"""
    state = _MediaState(transport, recording, resources)

    class Usage:
        def set_usage(self, usage):
            state.usage = usage
            if usage:
                state.transport.open()
                # 先记住当前资源，订阅时的立即回调不会误判为变化。
                state.applied_resources = resources.current()
                state.stops.append(recording.observe(lambda value: state._on_recording()))
                state.stops.append(resources.observe(lambda value: state._on_resources()))
                state.transport.listen(state._effective_listening())
            else:
                for stop in state.stops:
                    stop()
                state.stops.clear()
                state.applied_resources = None
                state._release()

        def set_listening(self, intent):
            state.listening_intent = intent
            if state.usage:
                state.transport.listen(state._effective_listening())

    return Usage()
