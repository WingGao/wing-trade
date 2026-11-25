from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.time import BEIJING_TZ

MarketSessionPhase = Literal["pre", "regular", "post", "closed"]

_SHANGHAI_TIMEZONE: tzinfo = BEIJING_TZ

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
        """判断当前时刻是否处于常规交易时段内。"""
        local = (value or datetime.now(self.timezone)).astimezone(self.timezone)
        if not self.is_trading_day(local):
            return False
        current = local.time().replace(tzinfo=None)
        return self.morning_open <= current <= self.morning_close or self.afternoon_open <= current <= self.afternoon_close

    def session_phase(self, value: datetime | None = None) -> MarketSessionPhase:
        """返回当前时刻所属交易阶段（盘中或休市）。"""
        return "regular" if self.is_session_open(value) else "closed"

    def cache_ttl(self, value: datetime | None = None) -> timedelta:
        """按当前是否处于活跃交易时段返回行情缓存有效期。"""
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
    """美股交易日历：覆盖盘前（04:00–09:30）、常规盘（09:30–16:00）与盘后（16:00–20:00）美东时段。"""

    timezone = _NEW_YORK_TIMEZONE
    pre_open = time(4, 0)
    morning_open = time(9, 30)
    morning_close = time(16, 0)
    afternoon_open = time(9, 30)
    afternoon_close = time(16, 0)
    post_close = time(20, 0)

    def session_phase(self, value: datetime | None = None) -> MarketSessionPhase:
        """返回美东当前时刻对应的美股交易阶段（盘前、盘中、盘后或休市）。"""
        local = (value or datetime.now(self.timezone)).astimezone(self.timezone)
        if not self.is_trading_day(local):
            return "closed"
        current = local.time().replace(tzinfo=None)
        if self.pre_open <= current < self.morning_open:
            return "pre"
        if self.morning_open <= current <= self.morning_close:
            return "regular"
        if self.morning_close < current <= self.post_close:
            return "post"
        return "closed"

    def is_session_open(self, value: datetime | None = None) -> bool:
        """美股盘前、常规盘及盘后均视为活跃行情时段。"""
        return self.session_phase(value) != "closed"


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

    def session_phase(self, value: datetime | None = None) -> MarketSessionPhase:
        """场外基金无盘中交易阶段，始终返回 closed。"""
        return "closed"

