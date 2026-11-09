"""ESP32 输出模式控制；连接入口与采集输出通道独立。"""
from dataclasses import dataclass

MODE_COMMANDS = {"serial": b"MODE SERIAL\n", "wifi": b"MODE WIFI\n"}
MODE_IDS = {1: "serial", 2: "wifi"}


@dataclass(frozen=True)
class ModeConfirmation:
    mode: str


def parse_mode_confirmation(packet: bytes) -> ModeConfirmation | None:
    if len(packet) == 3 and packet[:2] == b"\x10\x01" and packet[2] in MODE_IDS:
        return ModeConfirmation(MODE_IDS[packet[2]])
    return None
