"""测试公共夹具与默认配置路径隔离。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import app.config as config_module


@pytest.fixture(autouse=True)
def _isolate_default_yaml_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认隔离本机 config.yaml 与 CONFIG_FILE，避免本地配置污染单元测试。"""
    monkeypatch.delenv(config_module.CONFIG_FILE_ENV_NAME, raising=False)
    monkeypatch.setattr(config_module, "_default_config_candidates", lambda: ())


@pytest.fixture
def executed_at() -> datetime:
    """返回固定的测试成交时间。"""
    return datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
