# -*-coding: utf-8 -*-
"""esp32s3capture v1 通道协议校验（固件契约的服务端镜像）。

协议契约以 ``esp32s3capture/firmware`` 为唯一真源，本模块只做边界校验：
- ``capture`` 控制通道：UTF-8 JSON 文本（≤1024 字节），v=1，消息类型
  ``state`` / ``capture_result`` / ``set_capture``；
- ``jpeg`` 通道文本消息：``jpeg_run`` 代际标记；
- ``run`` 为 uint32 序号，0 保留为未启动，按模运算比较新旧。
"""

MAX_MESSAGE_BYTES = 1024
MAX_JPEG_RUN_MESSAGE_BYTES = 1024
MAX_RUN = 0xFFFFFFFF


class CaptureProtocolError(ValueError):
    """应用通道消息不符合 v1 必需字段。"""


class JpegRunProtocolError(ValueError):
    """JPEG 代际边界消息不符合 v1 必需字段。"""


def run_is_older(run: int, current: int) -> bool:
    """按 uint32 序号比较新旧；存活代际跨度远小于半个回绕周期。"""
    return run != current and (
        run == 0 or (current != 0 and ((run - current) & 0xFFFFFFFF) >= 0x80000000)
    )


def parse_jpeg_run_marker(message) -> int:
    """解析 ``{"v":1,"type":"jpeg_run","run":N}``，返回 uint32 代际号。"""
    if not isinstance(message, str):
        raise JpegRunProtocolError("JPEG 代际标记必须是文本消息")
    try:
        encoded = message.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise JpegRunProtocolError("JPEG 代际标记不是合法 UTF-8") from exc
    if len(encoded) > MAX_JPEG_RUN_MESSAGE_BYTES:
        raise JpegRunProtocolError("JPEG 代际标记超过 1024 UTF-8 字节")
    import json

    try:
        value = json.loads(message)
    except (json.JSONDecodeError, UnicodeError, TypeError) as exc:
        raise JpegRunProtocolError("JPEG 代际标记 JSON 解析失败") from exc
    if not isinstance(value, dict) or not {"v", "type", "run"} <= value.keys():
        raise JpegRunProtocolError("JPEG 代际标记缺少必需字段")
    if type(value["v"]) is not int or value["v"] != 1 or value["type"] != "jpeg_run":
        raise JpegRunProtocolError("不支持的 JPEG 代际标记")
    run = value["run"]
    if type(run) is not int or not 1 <= run <= MAX_RUN:
        raise JpegRunProtocolError("run 必须是大于 0 的 uint32")
    return run


def _required_fields(value, fields, name):
    if not isinstance(value, dict) or any(field not in value for field in fields):
        raise CaptureProtocolError(f"{name} 缺少必需字段")


def _exact_bool(value, name):
    if type(value) is not bool:
        raise CaptureProtocolError(f"{name} 必须是布尔值")


def _nonnegative_int(value, name):
    if type(value) is not int or value < 0:
        raise CaptureProtocolError(f"{name} 必须是非负整数")


def _run_number(value, name):
    _nonnegative_int(value, name)
    if value > MAX_RUN:
        raise CaptureProtocolError(f"{name} 必须在 uint32 范围内")


def _request_id(value):
    if not isinstance(value, str) or not value:
        raise CaptureProtocolError("id 必须是非空 UTF-8 字符串")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CaptureProtocolError("id 必须是合法 UTF-8 字符串") from exc
    if "\0" in value:
        raise CaptureProtocolError("id 不得包含 U+0000")
    if len(encoded) > 64:
        raise CaptureProtocolError("id 不得超过 64 UTF-8 字节")


def _error_value(value, name):
    if value is not None and not isinstance(value, str):
        raise CaptureProtocolError(f"{name} 必须是字符串或 null")


def _finite_number(value, name, *, positive=False):
    from math import isfinite

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptureProtocolError(f"{name} 必须是有限数值")
    try:
        finite = isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (positive and value <= 0):
        raise CaptureProtocolError(f"{name} 超出有效量程")


def _validate_sensor(value, name, measurement_fields):
    fields = ("valid", "sampled_at_us", *measurement_fields, "error")
    _required_fields(value, fields, name)
    _exact_bool(value["valid"], f"{name}.valid")
    sampled_at_us = value["sampled_at_us"]
    if value["valid"] or sampled_at_us is not None:
        _nonnegative_int(sampled_at_us, f"{name}.sampled_at_us")
    _error_value(value["error"], f"{name}.error")
    if value["valid"]:
        if value["error"] is not None:
            raise CaptureProtocolError(f"{name}.error 在 valid 时必须为 null")
        for field in measurement_fields:
            _finite_number(value[field], f"{name}.{field}", positive=(field == "bpm"))
    elif any(value[field] is not None for field in measurement_fields):
        raise CaptureProtocolError(f"{name} 无效测量的数值字段必须为 null")


def parse_capture_message(message) -> dict:
    """解码并校验一条 ``capture`` 通道文本消息；失败抛 CaptureProtocolError。"""
    import json

    if isinstance(message, str):
        try:
            encoded = message.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CaptureProtocolError("消息不是合法 UTF-8") from exc
        text = message
    elif isinstance(message, (bytes, bytearray, memoryview)):
        encoded = bytes(message)
        try:
            text = encoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CaptureProtocolError("消息不是合法 UTF-8") from exc
    else:
        raise CaptureProtocolError("消息必须是 UTF-8 文本")
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise CaptureProtocolError("消息超过 1024 UTF-8 字节")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, UnicodeError, TypeError) as exc:
        raise CaptureProtocolError("JSON 解析失败") from exc
    if not isinstance(value, dict) or type(value.get("v")) is not int or value.get("v") != 1:
        raise CaptureProtocolError("不支持的协议版本")
    message_type = value.get("type")
    if message_type == "set_capture":
        _required_fields(value, ("v", "type", "id", "enabled"), "set_capture")
        _request_id(value["id"])
        _exact_bool(value["enabled"], "enabled")
    elif message_type == "capture_result":
        _required_fields(
            value, ("v", "type", "id", "ok", "enabled", "run", "audio_base_ms", "error"),
            "capture_result",
        )
        _request_id(value["id"])
        _exact_bool(value["ok"], "ok")
        _exact_bool(value["enabled"], "enabled")
        _run_number(value["run"], "run")
        _nonnegative_int(value["audio_base_ms"], "audio_base_ms")
        _error_value(value["error"], "error")
        if value["ok"] and value["error"] is not None:
            raise CaptureProtocolError("成功结果 error 必须为 null")
    elif message_type == "state":
        _required_fields(
            value, ("v", "type", "enabled", "run", "audio_base_ms", "temperature", "heart_rate"),
            "state",
        )
        _exact_bool(value["enabled"], "enabled")
        _run_number(value["run"], "run")
        _nonnegative_int(value["audio_base_ms"], "audio_base_ms")
        _validate_sensor(value["temperature"], "temperature", ("ambient_c", "object_c"))
        _validate_sensor(value["heart_rate"], "heart_rate", ("bpm",))
    else:
        raise CaptureProtocolError("未知消息类型")
    return value


__all__ = [
    "CaptureProtocolError",
    "JpegRunProtocolError",
    "MAX_MESSAGE_BYTES",
    "parse_capture_message",
    "parse_jpeg_run_marker",
    "run_is_older",
]
