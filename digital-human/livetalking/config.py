###############################################################################
#  配置解析 — CLI 参数 + YAML 配置
###############################################################################

import argparse
import json
import os


DEFAULT_LLM_API_KEY = "sk-0bdafe8bc06740c6904b1032578f570d"


def str_or_int(value):
    """尝试转换为 int，失败则返回 str"""
    try:
        return int(value)
    except ValueError:
        return value


def parse_args(argv=None):
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="LiveTalking Digital Human Server")

    # ─── 音频 ──────────────────────────────────────────────────────────
    parser.add_argument('--fps', type=int, default=25, help="video fps, must be 25")
    parser.add_argument('-l', type=int, default=10)
    parser.add_argument('-m', type=int, default=8)
    parser.add_argument('-r', type=int, default=10)

    # ─── 画面 ──────────────────────────────────────────────────────────
    # parser.add_argument('--W', type=int, default=450, help="GUI width")
    # parser.add_argument('--H', type=int, default=450, help="GUI height")

    # ─── 数字人模型 ────────────────────────────────────────────────────
    parser.add_argument('--model', type=str, default='wav2lip',
                        help="avatar model: wav2lip (musetalk/ultralight 需自行实现)")
    parser.add_argument('--avatar_id', type=str, default='my_avatar',
                        help="avatar id in data/avatars")
    parser.add_argument('--batch_size', type=int, default=8, help="infer batch（Windows 建议 4-8）")
    parser.add_argument('--modelres', type=int, default=256)
    parser.add_argument('--modelfile', type=str, default='./models/wav2lip.pth')
    # 缓存要能装下"热集"，否则每次切段都在颠簸 —— 缺的帧会落到渲染线程上
    # 同步读盘（一帧 720x1280 PNG ≈ 11ms，而 25fps 每帧预算只有 40ms）。
    #
    # 实测热集大小（720x1280，每帧 BGR 2.64 MB）：
    #   my_avatar  3 个待机 710 + talk 225 + talk2 200 = 1135 帧 ≈ 2.9 GB
    #   yy         2 个待机 368 + talk 268              =  636 帧 ≈ 1.6 GB
    # 512（旧默认）装不下 my_avatar 的热集，所以切段/切待机时会卡。
    # 1600 帧 ≈ 4.2 GB，留了动作段与过渡在途的余量。
    parser.add_argument('--frame_cache_size', type=int, default=1600,
                        help="原始帧 LRU 缓存数量（每帧约 2.64 MB，1600 帧约 4.2 GB）")
    parser.add_argument('--face_cache_size', type=int, default=1024,
                        help="Wav2Lip 人脸帧 LRU 缓存数量（每帧约 0.2 MB，1024 帧约 200 MB）")
    parser.add_argument('--webrtc_video_queue_size', type=int, default=12,
                        help="WebRTC 视频抖动缓冲帧数（12 帧约 480ms）")
    parser.add_argument('--webrtc_audio_queue_size', type=int, default=24,
                        help="WebRTC 音频抖动缓冲包数（24 包约 480ms）")
    parser.add_argument('--webrtc_preroll_ms', type=float, default=120.0,
                        help="WebRTC 音视频共同预缓冲毫秒数")
    parser.add_argument('--initial_preload_frames', type=int, default=32,
                        help="实时会话首屏同步预载帧数，其余帧后台预取")

    # ─── 自定义动作和多形象 ────────────────────────────────────────────
    parser.add_argument('--customvideo_config', type=str, default='',
                        help="custom action json")

    # ─── TTS ───────────────────────────────────────────────────────────
    parser.add_argument('--tts', type=str, default='cosyvoice3',
                        help="tts plugin: cosyvoice3 (本地流式 Fun-CosyVoice3)")
    parser.add_argument('--REF_FILE', type=str, default="mp4/sound_ref.wav",
                        help="固定声音复刻参考音频")
    parser.add_argument(
        '--REF_TEXT',
        type=str,
        default="光之国的家是他们的故乡，初代佐菲杰克来自这地方",
        help="固定参考音频对应文本",
    )
    parser.add_argument(
        '--cosyvoice3_model_dir',
        type=str,
        default='models/Fun-CosyVoice3-0.5B',
        help="本地 Fun-CosyVoice3 模型目录",
    )
    parser.add_argument('--TTS_SERVER', type=str, default='',
                        help="保留参数；本地 cosyvoice3 不使用服务地址")
    parser.add_argument(
        '--TTS_SPK_ID',
        type=str,
        default='livetalking_fixed_voice',
        help="CosyVoice3 固定缓存音色 ID（仅作实时口型音色缺失时的兜底）",
    )
    parser.add_argument(
        '--live_spk_id',
        type=str,
        default='live_spk',
        help="实时口型模式使用的 CosyVoice3 音色 ID",
    )
    parser.add_argument(
        '--live_ref_file',
        type=str,
        default='mp4/sound_ref.wav',
        help="实时口型音色的参考音频（默认即初代佐菲杰克那条）；启动时用当前模型重新注册",
    )
    parser.add_argument(
        '--live_ref_text',
        type=str,
        default='光之国的家是他们的故乡，初代佐菲杰克来自这地方',
        help="参考音频的逐字文本，必须与音频内容完全一致，否则音色会失真",
    )
    parser.add_argument(
        '--live_spk_cache',
        type=str,
        default='',
        help="回退用：旧流程的 spk2info.pt 路径；留空则用 data/avatars/<avatar_id>/spk2info.pt",
    )
    parser.add_argument('--tts_speed', type=float, default=1.0,
                        help="CosyVoice3 语速倍率")
    parser.add_argument('--default_tts', type=str, default='cosyvoice3',
                        help="轻量页 TTS 引擎: cosyvoice3（本地稳定，默认）/ edge（微软内置音色，需联网且服务不稳定）")
    parser.add_argument('--default_tts_voice', type=str,
                        default='zh-CN-YunxiNeural',
                        help="轻量页 Edge TTS 音色，如 zh-CN-YunxiNeural（活泼少年）")

    # ─── 传输 ─────────────────────────────────────────────────────────
    parser.add_argument('--transport', type=str, default='webrtc',
                        help="output: rtcpush/webrtc/rtmp/virtualcam")
    parser.add_argument('--push_url', type=str,
                        default='http://localhost:1985/rtc/v1/whip/?app=live&stream=livestream')
    parser.add_argument('--max_session', type=int, default=1)
    parser.add_argument('--listenport', type=int, default=8010,
                        help="web listen port")

    # ─── LLM ───────────────────────────────────────────────────────────
    parser.add_argument(
        '--llm_base_url',
        type=str,
        default=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    )
    parser.add_argument(
        '--llm_api_key',
        type=str,
        default=os.getenv(
            "LLM_API_KEY",
            os.getenv("DASHSCOPE_API_KEY", DEFAULT_LLM_API_KEY),
        ),
    )
    parser.add_argument('--llm_model', type=str, default=os.getenv("LLM_MODEL", "deepseek-chat"))
    parser.add_argument('--llm_system_prompt', type=str, default='你是一个友好的数字人助手。')

    # ─── ASR ───────────────────────────────────────────────────────────
    parser.add_argument('--asr_model', type=str, default='small',
                        help="Whisper model: tiny/base/small/medium/large")
    parser.add_argument('--asr_device', type=str, default='auto')
    parser.add_argument('--asr_compute_type', type=str, default='auto')
    parser.add_argument('--asr_backend', type=str, default='funasr-http',
                        help='对话 ASR 后端：funasr-http（调 8081 常驻 SenseVoiceSmall，'
                             '中文准/纯简体/快 3~15x，失败自动回退 whisper）| openai-whisper')
    parser.add_argument('--mic_vad_threshold', type=float, default=0.012,
                        help='实时麦克风能量 VAD 阈值。历史值：0.018（太高，AEC 后 '
                             '语音 RMS 0.011~0.021 被切碎/漏检）→ 0.009（太灵敏，噪声'
                             '开轮）→ 0.012 折中：桌面噪声底之上、弱语音之下')
    parser.add_argument('--mic_vad_start_ms', type=int, default=250,
                        help='连续人声达到该时长后开始一句话（阈值降低后加长启动窗，防噪声误开轮）')
    parser.add_argument('--mic_vad_silence_ms', type=int, default=320,
                        help='连续静音达到该时长后结束一句话（原 850 偏保守，加上话尾'
                             '宽限后整轮判停 1.1s，用户听感是回复慢；650 与宽限 450 '
                             '组合仍留 ~1.1s 句中停顿容忍）')
    parser.add_argument('--mic_max_utterance_s', type=float, default=20.0,
                        help='单次麦克风输入最大秒数')
    parser.add_argument('--mic_reopen_grace_ms', type=int, default=250,
                        help='话尾宽限：该时长内继续开口会并回同一句话，避免抢答丢字'
                             '（ASR 与宽限并行跑，FunASR 数百毫秒即可转写完，'
                             '原 800 每轮白等约 0.3~0.5s）')

    # ─── 唤醒词 ────────────────────────────────────────────────────────
    parser.add_argument('--wake_word_default', type=str, default='你好',
                        help='轻量页麦克风唤醒词（与实时口型页统一为「你好」）；命中后才开始对话，留空禁用')
    parser.add_argument('--wake_word_live', type=str, default='你好',
                        help='实时口型页麦克风唤醒词；留空禁用')

    opt = parser.parse_args(argv)

    # ─── 后处理 ────────────────────────────────────────────────────────
    opt.customopt = []
    if opt.customvideo_config:
        with open(opt.customvideo_config, 'r') as f:
            opt.customopt = json.load(f)

    return opt
