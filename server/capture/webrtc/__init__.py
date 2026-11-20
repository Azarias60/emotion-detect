# -*-coding: utf-8 -*-
"""采集驱动内核（WebRTC 适配器层，规格 §2）。

对接 ``esp32s3capture`` 固件契约：WHIP ``POST /whip/capture`` 信令、
SRTP Opus 音频轨、
``jpeg`` 分块图像通道与 ``capture`` 传感器控制通道。业务层经
``CaptureKernel`` 的内存读端口消费，不感知传输细节；跨模块入口是
``capture/service.py`` 与 ``capture/types/``，本包根不重导出实现。
"""
