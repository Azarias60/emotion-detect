# -*-coding: utf-8 -*-
"""全局共享运行时状态、模型引用与并发锁。"""
import threading


# --- 1. 模型全局引用容器 ---
class ModelContainer:
    def __init__(self):
        self.predictor = None          # 人脸表情预测器
        self.voice_model = None         # FunASR 语音模型
        self.tongue_predictor = None    # 舌象四维预测模型


models = ModelContainer()

# --- 2. 并发互斥锁 ---
emotion_analysis_lock = threading.Lock()
health_analysis_lock = threading.Lock()
fatigue_analysis_lock = threading.Lock()
tongue_predictor_lock = threading.Lock()

# --- 3. 检测模块全局互斥（2026-11-11：健康问诊/疲劳检测/情绪识别互相不打扰） ---
# 三个模块共用一把全局锁：同一时刻只允许一个模块处于分析中。current_detection
# 供 /detection/status 状态条与 409 文案使用。
detection_global_lock = threading.Lock()
current_detection = {"module": None}
