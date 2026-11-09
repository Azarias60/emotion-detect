from threading import Thread
import queue
from queue import Queue
from io import BytesIO
from enum import Enum

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from avatars.base_avatar import BaseAvatar

from utils.logger import logger

class State(Enum):
    RUNNING = 0
    PAUSE = 1

class BaseTTS:
    def __init__(self, opt, parent: "BaseAvatar"):
        self.opt = opt
        self.parent = parent

        #self.fps = opt.fps # 20 ms per frame
        self.sample_rate = 16000
        self.chunk = self.sample_rate // (opt.fps*2) # 320 samples per chunk (20ms * 16000 / 1000)
        self.input_stream = BytesIO()

        self.msgqueue = Queue()
        self.state = State.RUNNING
        self.busy = False
        self.last_error = None

    def flush_talk(self):
        with self.msgqueue.mutex:
            self.msgqueue.queue.clear()
        self.state = State.PAUSE

    def put_msg_txt(self, msg: str, datainfo: dict | None = None):
        if len(msg) > 0:
            self.msgqueue.put((msg, datainfo or {}))

    def render(self, quit_event):
        process_thread = Thread(
            target=self.process_tts,
            args=(quit_event,),
            name=f"tts-{self.__class__.__name__}",
            daemon=True,
        )
        process_thread.start()
        self.process_thread = process_thread
    
    def process_tts(self, quit_event):
        while not quit_event.is_set():
            try:
                msg: tuple[str, dict] = self.msgqueue.get(block=True, timeout=1)
            except queue.Empty:
                continue
            # 丢弃过期回复的段落：打断后旧 LLM 线程还在送段落，
            # 不丢会与新回复的声音重叠播放
            stale_epoch = (
                msg[1].get("reply_epoch")
                if isinstance(msg[1], dict) else None
            )
            current_epoch = getattr(self.parent, "_reply_epoch", None)
            if (
                stale_epoch is not None
                and current_epoch is not None
                and stale_epoch < current_epoch
            ):
                logger.info("[TTS] 丢弃过期回复段: %.20r", msg[0])
                continue
            self.state = State.RUNNING
            self.last_error = None
            self.busy = True
            try:
                self.txt_to_audio(msg)
            except Exception as exc:
                self.last_error = exc
                logger.exception("TTS processing failed")
            finally:
                self.busy = False
        self.stop_tts()
        logger.info('ttsreal thread stop')
    
    def txt_to_audio(self, msg: tuple[str, dict]):
        pass

    def stop_tts(self):
        pass
