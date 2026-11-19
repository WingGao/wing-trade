from __future__ import annotations

import io
import logging

import pytest

from app.config import Settings

LOGGER_NAMES = ("ashare_ledger", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def restore_logging_state():
    """在测试结束后恢复全局日志记录器状态，避免影响其他测试。"""
    root_logger = logging.getLogger()
    loggers = (root_logger, *(logging.getLogger(name) for name in LOGGER_NAMES))
    original_states = [
        (logger, logger.level, logger.disabled, logger.propagate, list(logger.handlers))
        for logger in loggers
    ]
    yield
    for logger, level, disabled, propagate, handlers in original_states:
        new_handlers = [handler for handler in logger.handlers if handler not in handlers]
        logger.handlers[:] = handlers
        for handler in new_handlers:
            handler.close()
        logger.setLevel(level)
        logger.disabled = disabled
        logger.propagate = propagate


@pytest.fixture
def logging_configurer(restore_logging_state):
    """在日志状态快照建立后加载应用，避免测试导入污染全局配置。"""
    from app.main import configure_logging as apply_logging_config

    return apply_logging_config


def test_log_level_defaults_to_info(monkeypatch) -> None:
    """未配置 LOG_LEVEL 时，应用日志等级应保持为 INFO。"""
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    assert Settings.from_env().log_level == "INFO"


@pytest.mark.parametrize("raw_level", ["debug", "Info", "warning", "ERROR", "critical"])
def test_log_level_is_case_insensitive(monkeypatch, raw_level: str) -> None:
    """LOG_LEVEL 应忽略大小写并规范为标准等级名称。"""
    monkeypatch.setenv("LOG_LEVEL", raw_level)

    assert Settings.from_env().log_level == raw_level.upper()


def test_invalid_log_level_fails_with_environment_name(monkeypatch) -> None:
    """非法 LOG_LEVEL 应直接失败，且错误信息应指出配置项名称。"""
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(ValueError, match="LOG_LEVEL"):
        Settings.from_env()


def test_warning_level_filters_application_and_uvicorn_info(
    monkeypatch, logging_configurer, restore_logging_state
) -> None:
    """WARNING 等级应过滤应用日志和 Uvicorn 访问日志中的 INFO 记录。"""
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    logging_configurer(Settings.from_env().log_level)
    application_stream = io.StringIO()
    access_stream = io.StringIO()
    application_handler = logging.StreamHandler(application_stream)
    access_handler = logging.StreamHandler(access_stream)
    application_logger = logging.getLogger("ashare_ledger.log_level_probe")
    access_logger = logging.getLogger("uvicorn.access")
    access_logger_disabled = access_logger.disabled
    access_logger.disabled = False
    application_logger.addHandler(application_handler)
    access_logger.addHandler(access_handler)

    try:
        application_logger.info("hidden application info")
        application_logger.warning("visible application warning")
        access_logger.info("hidden access info")
        access_logger.warning("visible access warning")
    finally:
        application_logger.removeHandler(application_handler)
        access_logger.removeHandler(access_handler)
        access_logger.disabled = access_logger_disabled

    application_messages = application_stream.getvalue()
    access_messages = access_stream.getvalue()
    assert "hidden application info" not in application_messages
    assert "visible application warning" in application_messages
    assert "hidden access info" not in access_messages
    assert "visible access warning" in access_messages


def test_configure_logging_does_not_duplicate_handlers(
    logging_configurer, restore_logging_state
) -> None:
    """重复配置日志时不应新增根记录器或 Uvicorn 访问日志处理器。"""
    root_logger = logging.getLogger()
    access_logger = logging.getLogger("uvicorn.access")
    root_logger.handlers[:] = []
    access_handler = logging.StreamHandler(io.StringIO())
    access_logger.handlers[:] = [access_handler]
    access_logger.propagate = False

    logging_configurer("INFO")
    root_handlers = tuple(root_logger.handlers)
    logging_configurer("WARNING")

    assert len(root_handlers) == 1
    assert tuple(root_logger.handlers) == root_handlers
    assert access_logger.handlers == [access_handler]


@pytest.mark.parametrize("logger_name", LOGGER_NAMES)
def test_configure_logging_applies_level_to_each_logger(
    logger_name: str, logging_configurer, restore_logging_state
) -> None:
    """应用和 Uvicorn 命名日志记录器应统一采用配置的等级。"""
    logging_configurer("debug")

    assert logging.getLogger(logger_name).getEffectiveLevel() == logging.DEBUG
