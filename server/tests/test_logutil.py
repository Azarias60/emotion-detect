"""Logger initialization is idempotent without a separate module-level flag."""

import logging

from server.logutil import get_logger


def test_get_logger_installs_only_one_namespace_handler():
    root = logging.getLogger("physiosense")
    original_handlers = root.handlers[:]
    original_level = root.level
    original_propagate = root.propagate
    try:
        for handler in root.handlers[:]:
            root.removeHandler(handler)

        first = get_logger("test.first")
        handler = root.handlers[0]
        second = get_logger("test.second")

        assert first.name == "physiosense.test.first"
        assert second.name == "physiosense.test.second"
        assert root.handlers == [handler]
        assert root.level == logging.INFO
        assert root.propagate is False
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)
        root.propagate = original_propagate


def test_existing_namespace_handler_is_not_replaced():
    root = logging.getLogger("physiosense")
    original_handlers = root.handlers[:]
    original_level = root.level
    original_propagate = root.propagate
    existing = logging.NullHandler()
    try:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        root.addHandler(existing)
        get_logger("test.existing")
        assert root.handlers == [existing]
    finally:
        root.removeHandler(existing)
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)
        root.propagate = original_propagate
