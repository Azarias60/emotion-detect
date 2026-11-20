"""检测意图契约测试共享的驱动步骤。

启动前置（模型替身、设备在线、望诊画面）与采集窗口帧的时机都由这里
统一安排：health 的望诊在启动准备里消费启动前最近 2 秒画面；测量消费
封存记录内的帧（启动后种帧才进本轮记录）。封存、轮询与报告读取归
``detection_client``。
"""

from server.tests.detection_client import auth_headers, begin
from server.tests.runtime_fixture import attach_fake_device, seed_frame


def start_detection(client, request, kind, username, *, emotions=("happy",), frames=5):
    """按模式装好启动前置并启动检测，返回 (headers, report_id)。

    ``emotions`` 传 None 表示不装载表情模型（供缺失语义用例使用）；
    ``frames`` 是启动后写入本轮采集记录的窗口帧数。
    """
    attach_fake_device()
    if kind == "emotion":
        if emotions is not None:
            request.getfixturevalue("face_predictor")(emotions)
    elif kind == "health":
        request.getfixturevalue("tongue_model")()
        seed_frame()
    headers = auth_headers(client, username)
    report_id = begin(client, headers, kind)
    for _ in range(frames):
        seed_frame()
    return headers, report_id
