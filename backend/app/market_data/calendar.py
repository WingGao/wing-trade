from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SHANGHAI_TIMEZONE: tzinfo
try:
    _SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    # Asia/Shanghai has no DST transitions. Keep Windows/offline environments
    # usable when the optional system tzdata package is unavailable.
    _SHANGHAI_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")

_NEW_YORK_TIMEZONE: tzinfo
try:
    _NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    _NEW_YORK_TIMEZONE = timezone(timedelta(hours=-5), "America/New_York")


class AShareTradingCalendar:
    """沪深 regular-session calendar with injectable holidays for tests."""

    timezone = _SHANGHAI_TIMEZONE
    morning_open = time(9, 15)
    morning_close = time(11, 30)
    afternoon_open = time(13, 0)
    afternoon_close = time(15, 0)

    def __init__(self, holidays: set[date] | None = None, *, trading_ttl_seconds: int = 10, closed_ttl_seconds: int = 3600) -> None:
        self.holidays = holidays or set()
        self.trading_ttl_seconds = trading_ttl_seconds
        self.closed_ttl_seconds = closed_ttl_seconds

    def is_trading_day(self, value: date | datetime) -> bool:
        day = value.date() if isinstance(value, datetime) else value
        return day.weekday() < 5 and day not in self.holidays

    def is_session_open(self, value: datetime | None = None) -> bool:
        local = (value or datetime.now(self.timezone)).astimezone(self.timezone)
        if not self.is_trading_day(local):
            return False
        current = local.time().replace(tzinfo=None)
        return self.morning_open <= current <= self.morning_close or self.afternoon_open <= current <= self.afternoon_close

    def cache_ttl(self, value: datetime | None = None) -> timedelta:
        return timedelta(seconds=self.trading_ttl_seconds if self.is_session_open(value) else self.closed_ttl_seconds)

    def is_stale(self, fetched_at: datetime, *, now: datetime | None = None) -> bool:
        """按交易时段缓存期限判断获取时间是否过期。"""
        current = now or datetime.now(self.timezone)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current - fetched_at.astimezone(current.tzinfo) > self.cache_ttl(current)


class USTradingCalendar(AShareTradingCalendar):
    """US regular session calendar for long-only stocks and ETFs."""

    timezone = _NEW_YORK_TIMEZONE
    morning_open = time(9, 30)
    morning_close = time(16, 0)
    afternoon_open = time(9, 30)
    afternoon_close = time(16, 0)


class OpenFundCalendar(AShareTradingCalendar):
    """场外开放式基金日历：仅依赖收盘后公布的单位净值，不参与盘中实时刷新。"""

    def __init__(
        self,
        holidays: set[date] | None = None,
        *,
        nav_ttl_seconds: int = 6 * 3600,
    ) -> None:
        """初始化场外基金净值缓存有效期，避免盘中按分钟级误判过期。"""
        super().__init__(
            holidays=holidays,
            trading_ttl_seconds=nav_ttl_seconds,
            closed_ttl_seconds=nav_ttl_seconds,
        )

    def is_session_open(self, value: datetime | None = None) -> bool:
        """场外基金无盘中连续竞价时段，始终返回 False。"""
        return False

