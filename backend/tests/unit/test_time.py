"""后端统一北京时间与强时区参数校验单元测试。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.api.schemas.trade import BuyTradeRequest, TradeUpdateRequest
from app.domain.time import (
    BEIJING_TZ,
    assume_utc,
    format_beijing_iso,
    now_beijing,
    now_utc,
    require_aware_utc,
    to_beijing,
)
from app.main import BeijingFormatter


def test_require_aware_utc_accepts_offset_and_normalizes_to_utc() -> None:
    """带 +08:00 或 Z 时区的入参应被规范化为对应 UTC 时刻。"""
    shanghai_dt = datetime(2026, 11, 25, 9, 30, 15, 123456, tzinfo=BEIJING_TZ)
    normalized = require_aware_utc(shanghai_dt)
    assert normalized == datetime(2026, 11, 25, 1, 30, 15, 123456, tzinfo=UTC)

    ny_tz = timezone(timedelta(hours=-5))
    ny_dt = datetime(2026, 11, 24, 20, 30, 0, tzinfo=ny_tz)
    assert require_aware_utc(ny_dt) == datetime(2026, 11, 25, 1, 30, 0, tzinfo=UTC)


def test_require_aware_utc_rejects_naive_datetime() -> None:
    """无时区的 naive datetime 入参必须被拒绝。"""
    naive = datetime(2026, 11, 25, 9, 30, 0)
    with pytest.raises(ValueError, match="时区"):
        require_aware_utc(naive)


def test_schema_rejects_naive_executed_at_and_normalizes_aware() -> None:
    """交易请求 Schema 必须拒绝无时区时间，并将合法带时区时间归一化为 UTC。"""
    with pytest.raises(ValueError, match="时区"):
        BuyTradeRequest(
            security_id="sec-1",
            executed_at="2026-11-25T09:30:00",  # type: ignore[arg-type]
            qty="100",  # type: ignore[arg-type]
            price="10.00",  # type: ignore[arg-type]
        )

    req = BuyTradeRequest(
        security_id="sec-1",
        executed_at="2026-11-25T09:30:00+08:00",  # type: ignore[arg-type]
        qty="100",  # type: ignore[arg-type]
        price="10.00",  # type: ignore[arg-type]
    )
    assert req.executed_at == datetime(2026, 11, 25, 1, 30, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="时区"):
        TradeUpdateRequest(
            revision=1,
            executed_at="2026-11-25T09:30:00",  # type: ignore[arg-type]
        )


def test_format_beijing_iso_converts_utc_and_naive_db_values_preserving_microseconds() -> None:
    """数据库历史无时区 UTC 值与 UTC aware 值应统一输出为带 +08:00 的北京时间且保留微秒。"""
    naive_db_utc = datetime(2026, 11, 25, 3, 47, 0, 651483)
    assert format_beijing_iso(naive_db_utc) == "2026-11-25T11:47:00.651483+08:00"

    aware_utc = datetime(2026, 11, 25, 1, 30, 0, tzinfo=UTC)
    assert format_beijing_iso(aware_utc) == "2026-11-25T09:30:00+08:00"
    assert format_beijing_iso(None) is None


def test_assume_utc_and_now_helpers() -> None:
    """辅助函数返回正确的时区偏移与等价绝对时刻。"""
    naive = datetime(2026, 11, 25, 0, 0, 0)
    assert assume_utc(naive).tzinfo == UTC
    assert to_beijing(naive).utcoffset() == timedelta(hours=8)
    assert now_utc().utcoffset() == timedelta(0)
    assert now_beijing().utcoffset() == timedelta(hours=8)


def test_beijing_log_formatter_outputs_plus_0800() -> None:
    """日志格式器输出的时间戳应携带 +08:00 北京时区偏移。"""
    formatter = BeijingFormatter("%(asctime)s %(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.created = datetime(2026, 11, 25, 1, 30, 0, tzinfo=UTC).timestamp()
    formatted = formatter.formatTime(record)
    assert formatted.startswith("2026-11-25T09:30:00")
    assert formatted.endswith("+08:00")
