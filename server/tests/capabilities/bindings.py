"""测试侧接入表，不是产品目录或服务数量规范。

实现可以修改此处的导入和纯调用映射，以接入不同组织方式的真实能力。
此处不得实现状态、计时、取消、值修改或变化通知来代替被测职责。
各用例中表达的调用含义和断言按统一计划保持稳定。
"""
from importlib import import_module


ENTRIES = {
    "detection": ("server.detection.service", "create_detection_execution"),
    "context": ("server.conversation.service", "create_context"),
    "media": ("server.conversation.media", "create_media_usage"),
    "objects": ("server.support.resources.service", "create_objects"),
    "task": ("server.production.service", "create_task"),
    "recording": ("server.recording.service", "create_recording"),
    "model_config": ("server.support.model_config.service", "create_model_config"),
    "observation": ("server.presentation.observation", "create_observation_connection"),
}


def capability(name, **dependencies):
    module_name, factory_name = ENTRIES[name]
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name == module_name or module_name.startswith(error.name + "."):
            raise AssertionError(f"待接入真实能力：{name}；测试侧绑定 {module_name}:{factory_name}") from None
        raise
    factory = getattr(module, factory_name, None)
    assert callable(factory), f"待接入真实能力：{name}；测试侧绑定 {module_name}:{factory_name}"
    return factory(**dependencies)
