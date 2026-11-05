from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


class AShareTradingCalendar:
    """沪深 regular-session calendar with injectable holidays for tests."""

    timezone = ZoneInfo("Asia/Shanghai")
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
        current = now or datetime.now(self.timezone)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=self.timezone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=self.timezone)
        return current - fetched_at.astimezone(current.tzinfo) > self.cache_ttl(current)

