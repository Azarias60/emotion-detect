from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace

from server.webrtc import PlayerStreamTrack


class AVSyncTests(unittest.TestCase):
    def test_webrtc_tracks_share_zero_point_and_exact_media_steps(self):
        player = SimpleNamespace(media_epoch=time.monotonic() - 1)
        video = PlayerStreamTrack(player, "video")
        audio = PlayerStreamTrack(player, "audio")

        async def timestamps():
            return (
                await video.next_timestamp(),
                await audio.next_timestamp(),
                await audio.next_timestamp(),
                await video.next_timestamp(),
            )

        video_0, audio_0, audio_1, video_1 = asyncio.run(timestamps())
        self.assertEqual((0, video_0[1]), video_0)
        self.assertEqual((0, audio_0[1]), audio_0)
        self.assertEqual(320, audio_1[0])
        self.assertEqual(3600, video_1[0])
        self.assertEqual(12, video._queue.maxsize)
        self.assertEqual(24, audio._queue.maxsize)


if __name__ == "__main__":
    unittest.main()
