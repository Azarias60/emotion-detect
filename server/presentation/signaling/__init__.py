"""设备入流 WHIP 信令适配（presentation/signaling）。

只做设备协议解析与应答、请求准入和错误映射；会话协商与发布调用
capture 公开入口，不在本层持有设备连接状态。
"""
