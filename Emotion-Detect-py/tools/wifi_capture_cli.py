# -*-coding: utf-8 -*-
"""ESP32 WiFi 多模态采集独立命令行调试工具。

基于 Emotion-Detect-py 的 WiFiTransport 和 MCUClient 体系构建，
提供类似 采集-wifi.py 的控制台实时监控与 OpenCV 图像预览体验。

使用方式:
    python tools/wifi_capture_cli.py --ip 192.168.8.37 --tcp-port 8888 --udp-port 8889
"""
import argparse
import os
import sys
import time

# 将工程主目录加入路径
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(CURRENT_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import cv2
from core.config import WIFI_CONFIG
from mcu.client import MCUClient
from mcu.transport import WiFiTransport


def parse_args():
    parser = argparse.ArgumentParser(description="ESP32 WiFi 多模态数据采集与实时调试工具")
    parser.add_argument(
        "--ip",
        type=str,
        default=WIFI_CONFIG["host"],
        help=f"ESP32 设备 IP 地址 (默认: {WIFI_CONFIG['host']})",
    )
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=WIFI_CONFIG["tcp_port"],
        help=f"TCP 控制与数据端口 (默认: {WIFI_CONFIG['tcp_port']})",
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=WIFI_CONFIG["udp_port"],
        help=f"UDP 音频流监听端口 (默认: {WIFI_CONFIG['udp_port']})",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="不弹出 OpenCV 画面窗口，仅在终端打印传感器数值",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    print("=" * 60)
    print("  ESP32 WiFi 多模态数据采集调试工具 (Emotion-Detect-py)")
    print("=" * 60)
    print(f"目标设备 IP  : {args.ip}")
    print(f"TCP 数据端口 : {args.tcp_port} (图像/心率/红外温度)")
    print(f"UDP 音频端口 : {args.udp_port} (48kHz 16-bit 单声道 PCM)")
    print("提示: 按 Ctrl+C 或在画面窗口中按 'q' 键退出")
    print("-" * 60)

    transport = WiFiTransport(
        host=args.ip,
        tcp_port=args.tcp_port,
        udp_port=args.udp_port,
        timeout=0.1,
        connect_timeout=5.0,
    )
    client = MCUClient(transport=transport)
    client.start()

    window_name = "ESP32 Stream (WiFi Mode)"
    last_print_time = 0.0

    try:
        print("正在等待与 ESP32 建立连接...")
        while not client.is_connected:
            time.sleep(0.5)
            if client.is_stopped():
                print("连接终止。")
                return

        print("✅ WiFi 通信已建立，正在接收多模态数据流...")

        while True:
            # 1. 显示视频画面
            if not args.no_gui:
                frame = client.get_latest_frame(copy=True)
                if frame is not None:
                    # 在画面左上角绘制连接信息
                    cv2.putText(
                        frame,
                        f"WiFi: {args.ip}:{args.tcp_port}",
                        (15, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 255, 0),
                        2,
                    )
                    cv2.imshow(window_name, frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q") or key == 27:  # 'q' 或 ESC 退出
                        break
                else:
                    cv2.waitKey(10)

            # 2. 终端定时打印传感器读数 (每 0.5 秒)
            now = time.time()
            if now - last_print_time >= 0.5:
                last_print_time = now
                hr_summary = client.get_heart_rate_summary()
                temp_summary = client.get_temperature_summary()
                audio_summary = client.get_audio_summary()

                hr_str = f"{hr_summary['latest_hr']:.1f} BPM" if hr_summary['latest_hr'] else "--"
                amb_str = f"{temp_summary['latest_ambient_temp']:.2f} C" if temp_summary['latest_ambient_temp'] is not None else "--"
                obj_str = f"{temp_summary['latest_object_temp']:.2f} C" if temp_summary['latest_object_temp'] is not None else "--"
                vol_str = f"{audio_summary['latest_volume']:.1f}%"

                print(
                    f"\r[WiFi 实时] 心率: {hr_str:>9} | 环境温: {amb_str:>8} | 体温/物温: {obj_str:>8} | 音量: {vol_str:>6}",
                    end="",
                    flush=True,
                )

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n\n用户中断采集。")
    finally:
        print("\n正在停止并清理资源...")
        client.stop()
        if not args.no_gui:
            cv2.destroyAllWindows()
        print("采集已停止。")


if __name__ == "__main__":
    main()
