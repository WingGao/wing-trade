"""统一时区与时间处理工具：底层按 UTC 存取，对外边界统一北京时间（+08:00）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _load_beijing_timezone() -> tzinfo:
    """加载北京时间时区（Asia/Shanghai），在缺少系统 tzdata 时回退为固定 UTC+8。"""
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), "Asia/Shanghai")


BEIJING_TZ: tzinfo = _load_beijing_timezone()


def now_utc() -> datetime:
    """返回当前带 UTC 时区的时刻，用于数据库存储与时间差计算。"""
    return datetime.now(UTC)


def now_beijing() -> datetime:
    """返回当前带北京时区（+08:00）的时刻。"""
    return datetime.now(BEIJING_TZ)


def assume_utc(value: datetime) -> datetime:
    """将数据库读出或内部传递的时间归一化为带 UTC 时区的时刻。"""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def require_aware_utc(value: datetime) -> datetime:
    """校验接口时间入参必须携带显式时区偏移，并归一化为 UTC 时刻。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间参数必须包含显式时区信息，例如 +08:00 或 Z")
    return value.astimezone(UTC)


def to_beijing(value: datetime) -> datetime:
    """将任意时刻（无时区按数据库 UTC 语义还原）转换为带北京时区（+08:00）的时刻。"""
    return assume_utc(value).astimezone(BEIJING_TZ)


def format_beijing_iso(value: datetime | None) -> str | None:
    """将时间统一序列化为带 +08:00 偏移的北京时间 ISO-8601 字符串，保留微秒精度。"""
    if value is None:
        return None
    return to_beijing(value).isoformat()
