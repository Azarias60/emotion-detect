###############################################################################
#  Copyright (C) 2024 LiveTalking@lipku https://github.com/lipku/LiveTalking
#  email: lipku@foxmail.com
# 
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  
#       http://www.apache.org/licenses/LICENSE-2.0
# 
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

import asyncio
import json
import logging
import threading
import time
from typing import Tuple, Dict, Optional, Set, Union
import queue
from av.frame import Frame
from av.packet import Packet
from av import AudioFrame
import fractions
import numpy as np

AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCK_RATE = 90000
VIDEO_PTIME = 0.040 #1 / 25  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)
SAMPLE_RATE = 16000
AUDIO_TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)

#from aiortc.contrib.media import MediaPlayer, MediaRelay
#from aiortc.rtcrtpsender import RTCRtpSender
from aiortc import (
    MediaStreamTrack,
)

logging.basicConfig()
logger = logging.getLogger(__name__)
from utils.logger import logger as mylogger


class PlayerStreamTrack(MediaStreamTrack):
    """
    A video track that returns an animated flag.
    """

    def __init__(self, player, kind, queue_size=None):
        super().__init__()  # don't forget this!
        self.kind = kind
        self._player = player
        # Bounded queues prevent several seconds of stale media accumulating
        # when inference briefly falls behind.
        default_size = 24 if kind == "audio" else 12
        self._queue = queue.Queue(maxsize=max(2, int(queue_size or default_size)))
        self.timelist = [] #记录最近包的时间戳
        self.current_frame_count = 0
        if self.kind == 'video':
            self.framecount = 0
            self.lasttime = time.perf_counter()
            self.totaltime = 0
    
    _start: float
    _timestamp: int

    async def next_timestamp(self) -> Tuple[int, fractions.Fraction]:
        if self.readyState != "live":
            raise Exception

        if self.kind == "video":
            ptime = VIDEO_PTIME
            clock_rate = VIDEO_CLOCK_RATE
            time_base = VIDEO_TIME_BASE
        else:
            ptime = AUDIO_PTIME
            clock_rate = SAMPLE_RATE
            time_base = AUDIO_TIME_BASE

        frame_index = self.current_frame_count
        self.current_frame_count += 1
        timestamp = frame_index * int(ptime * clock_rate)
        target = self._player.media_epoch + frame_index * ptime
        wait = target - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        return timestamp, time_base

    async def recv(self) -> Union[Frame, Packet]:
        # frame = self.frames[self.counter % 30]            
        self._player._start(self)
        # if self.kind == 'video':
        #     frame = await self._queue.get()
        # else: #audio
        #     if hasattr(self, "_timestamp"):
        #         wait = self._start + self._timestamp / SAMPLE_RATE + AUDIO_PTIME - time.time()
        #         if wait>0:
        #             await asyncio.sleep(wait)
        #         if self._queue.qsize()<1:
        #             #frame = AudioFrame(format='s16', layout='mono', samples=320)
        #             audio = np.zeros((1, 320), dtype=np.int16)
        #             frame = AudioFrame.from_ndarray(audio, layout='mono', format='s16')
        #             frame.sample_rate=16000
        #         else:
        #             frame = await self._queue.get()
        #     else:
        while True:
            try:
                frame, eventpoint = self._queue.get_nowait()
                break
            except queue.Empty:
                await asyncio.sleep(0.002)
                
        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        if eventpoint and self._player is not None:
            self._player.notify(eventpoint)
        if frame is None:
            self.stop()
            raise Exception
        if self.kind == 'video':
            self.totaltime += (time.perf_counter() - self.lasttime)
            self.framecount += 1
            self.lasttime = time.perf_counter()
            if self.framecount==100:
                mylogger.info(f"------actual avg final fps:{self.framecount/self.totaltime:.4f}")
                self.framecount = 0
                self.totaltime=0
        return frame
    
    def stop(self):
        super().stop()
        # Drain & delete remaining frames
        while not self._queue.empty():
            item = self._queue.get_nowait()
            del item
        if self._player is not None:
            self._player._stop(self)
            self._player = None

def player_worker_thread(
    quit_event,
    container
):
    container.render(quit_event)

class HumanPlayer:

    def __init__(
        self, avatar_session, format=None, options=None, timeout=None, loop=False, decode=True
    ):
        self.__thread: Optional[threading.Thread] = None
        self.__thread_quit: Optional[threading.Event] = None

        # examine streams
        self.__started: Set[PlayerStreamTrack] = set()
        self.__audio: Optional[PlayerStreamTrack] = None
        self.__video: Optional[PlayerStreamTrack] = None

        self.__container = avatar_session
        session_opt = getattr(avatar_session, "opt", None)
        audio_queue_size = getattr(
            session_opt, "webrtc_audio_queue_size", 24
        )
        video_queue_size = getattr(
            session_opt, "webrtc_video_queue_size", 12
        )
        self._preroll_seconds = max(
            0.0,
            float(getattr(session_opt, "webrtc_preroll_ms", 120.0))
            / 1000.0,
        )
        self.__audio = PlayerStreamTrack(
            self, kind="audio", queue_size=audio_queue_size
        )
        self.__video = PlayerStreamTrack(
            self, kind="video", queue_size=video_queue_size
        )
        self._media_epoch: Optional[float] = None
        self._media_epoch_lock = threading.Lock()

        if hasattr(self.__container, 'output'):
            self.__container.output._player = self

    def push_video(self, frame):
        from av import VideoFrame
        new_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        self.__video._queue.put((new_frame, None))

    def push_audio(self, frame, eventpoint=None):
        from av import AudioFrame
        new_frame = AudioFrame(format='s16', layout='mono', samples=frame.shape[0])
        new_frame.planes[0].update(frame.tobytes())
        new_frame.sample_rate = 16000
        self.__audio._queue.put((new_frame, eventpoint))

    def get_buffer_size(self) -> int:
        return self.__video._queue.qsize()

    def clear_queues(self) -> None:
        """Drop media already queued for a cancelled response."""
        for media_track in (self.__audio, self.__video):
            while True:
                try:
                    media_track._queue.get_nowait()
                except queue.Empty:
                    break

    @property
    def media_epoch(self) -> float:
        """Return one monotonic epoch shared by audio and video RTP tracks."""
        with self._media_epoch_lock:
            if self._media_epoch is None:
                # A short common preroll gives both sender tracks time to start.
                self._media_epoch = (
                    time.monotonic() + self._preroll_seconds
                )
                mylogger.info(
                    "WebRTC shared media epoch: %.6f", self._media_epoch
                )
            return self._media_epoch

    def notify(self,eventpoint):
        if self.__container is not None:
            self.__container.notify(eventpoint)

    @property
    def audio(self) -> MediaStreamTrack:
        """
        A :class:`aiortc.MediaStreamTrack` instance if the file contains audio.
        """
        return self.__audio

    @property
    def video(self) -> MediaStreamTrack:
        """
        A :class:`aiortc.MediaStreamTrack` instance if the file contains video.
        """
        return self.__video

    def _start(self, track: PlayerStreamTrack) -> None:
        self.__started.add(track)
        if self.__thread is None:
            self.__log_debug("Starting worker thread")
            self.__thread_quit = threading.Event()
            self.__thread = threading.Thread(
                name="media-player",
                target=player_worker_thread,
                args=(
                    self.__thread_quit,
                    self.__container
                ),
                daemon=True,
            )
            self.__thread.start()

    def _stop(self, track: PlayerStreamTrack) -> None:
        self.__started.discard(track)

        if not self.__started and self.__thread is not None:
            self.__log_debug("Stopping worker thread")
            # Detach output before asking the renderer to stop.  Otherwise its
            # last frame can block forever on a full queue with no WebRTC
            # consumer, freezing aiohttp's event loop during mode switches.
            container = self.__container
            if (
                container is not None
                and hasattr(container, "output")
                and getattr(container.output, "_player", None) is self
            ):
                container.output._player = None
            for media_track in (self.__audio, self.__video):
                while not media_track._queue.empty():
                    try:
                        media_track._queue.get_nowait()
                    except queue.Empty:
                        break

            self.__thread_quit.set()
            worker = self.__thread
            self.__thread = None

            def finish_worker() -> None:
                worker.join(timeout=10)
                if worker.is_alive():
                    mylogger.warning(
                        "WebRTC renderer did not stop within 10 seconds"
                    )
                else:
                    self.__log_debug("Worker thread stopped")

            # MediaStreamTrack.stop() runs in the WebRTC event loop.  Never
            # block that loop while GPU/render workers wind down.
            threading.Thread(
                target=finish_worker,
                name="media-player-cleanup",
                daemon=True,
            ).start()

        if not self.__started and self.__container is not None:
            #self.__container.close()
            self.__container = None

    def __log_debug(self, msg: str, *args) -> None:
        mylogger.debug(f"HumanPlayer {msg}", *args)
