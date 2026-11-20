# -*-coding: utf-8 -*-
"""Capture 模块：WebRTC 采集内核与领域摘要。

设备契约的唯一真源是 ``esp32s3capture/firmware``；本包的 ``webrtc/``
子包实现采集驱动内核（信令、会话中枢、JPEG 重组、Opus 解码、传感器
校验），``domain`` 提供业务消费的冻结摘要对象。历史串口/COBS/HTTP 胶水
已随 esp32s3capture 切换彻底移除。跨模块公开入口是 ``service.py`` 与
``types/``；包根不重导出内部实现。
"""
