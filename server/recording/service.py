"""现场克隆录音：未提交录音的使用期归录音界面，提交后归任务。

开始、停止提交与取消是各自意图；使用期结束取消未提交录音，不自动
上传或启动克隆。明确提交把音频交给任务端口，任务生存期归制作能力。
"""


class _RecordingState:
    def __init__(self, audio, submit):
        self.audio = audio
        self.submit = submit
        self.usage = False
        self.handle = None
        self.submitted = False


def create_recording(audio, submit):
    """装配一份现场录音；audio 提供开始/完成/取消，submit 交付任务端口。"""
    state = _RecordingState(audio, submit)

    class Recording:
        def set_usage(self, usage):
            state.usage = usage
            if not usage and state.handle is not None and not state.submitted:
                # 使用期结束：取消未提交录音，不自动提交。
                audio.cancel(state.handle)
                state.handle = None

        def start(self):
            if state.handle is not None:
                return state.handle
            state.submitted = False
            state.handle = audio.start()
            return state.handle

        def stop_and_submit(self):
            if state.handle is None:
                raise RuntimeError("当前没有进行中的录音")
            captured = audio.finish(state.handle)
            state.handle = None
            state.submitted = True
            # 提交即交接：任务的执行与取消归制作能力。
            return submit(captured)

        def cancel(self):
            if state.handle is not None:
                audio.cancel(state.handle)
                state.handle = None

    return Recording()
