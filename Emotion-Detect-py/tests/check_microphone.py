# -*-coding: utf-8 -*-
"""麦克风独立采集测试脚本。用于验证系统麦克风/音频输入通道是否正常工作。"""
import os
import sys
import wave

try:
    import pyaudio
except ImportError:
    pyaudio = None

FORMAT = 16  # paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 256
RECORD_SECONDS = 5
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "mic_test_output.wav")


def test_microphone():
    if pyaudio is None:
        print("未检测到 pyaudio 库，请先安装: pip install pyaudio")
        sys.exit(1)

    audio = pyaudio.PyAudio()
    stream = None

    try:
        stream = audio.open(
            format=audio.get_format_from_width(2),
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )

        print(f"🎤 麦克风测试开始，录制 {RECORD_SECONDS} 秒音频...")
        frames = []

        total_chunks = int(RATE / CHUNK * RECORD_SECONDS)
        for i in range(total_chunks):
            data = stream.read(CHUNK)
            frames.append(data)
            progress = ((i + 1) / total_chunks) * 100
            print(f"\r录制进度: {progress:.1f}%", end="", flush=True)

        print("\n✅ 麦克风录制完成！")

        stream.stop_stream()
        stream.close()
        audio.terminate()

        wf = wave.open(OUTPUT_FILE, "wb")
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(b"".join(frames))
        wf.close()

        print(f"💾 音频已保存至: {OUTPUT_FILE}")
        print("👉 播放该音频文件即可确认麦克风是否正常输入。")

    except Exception as e:
        print(f"\n❌ 麦克风测试出错: {e}")
        print("常见原因：麦克风未连接、权限不足、设备被占用。")
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    test_microphone()
