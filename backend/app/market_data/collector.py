from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.ledger import Security

from .calendar import AShareTradingCalendar
from .provider import QuoteProvider, QuoteSnapshot
from .service import QuoteService

logger = logging.getLogger("ashare_ledger.market_data")


@dataclass(slots=True)
class CircuitBreakerState:
    failures: int = 0
    open_until: datetime | None = None
    backoff_seconds: int = 30

    @property
    def is_open(self) -> bool:
        """返回熔断器在当前时刻是否开启。"""
        return self.is_open_at(datetime.now(timezone.utc))

    def is_open_at(self, value: datetime) -> bool:
        """按指定时刻判断熔断器是否仍处于退避期。"""
        return self.open_until is not None and value < self.open_until


class QuoteCollector:
    """Single-flight quote refresh with exponential backoff and stale fallback."""

    def __init__(
        self,
        provider: QuoteProvider,
        service: QuoteService | None = None,
        *,
        calendar: AShareTradingCalendar | None = None,
        min_refresh_interval_seconds: int = 10,
    ) -> None:
        """初始化共享行情源、缓存服务、时钟边界和熔断状态。"""
        self.provider = provider
        self.service = service or QuoteService(calendar)
        self.calendar = calendar or self.service.calendar
        self.min_refresh_interval_seconds = min_refresh_interval_seconds
        self.last_refresh_at: datetime | None = None
        self.lock = asyncio.Lock()
        self.breaker = CircuitBreakerState()

    def _open_breaker(self, now: datetime) -> None:
        """记录行情失败并在连续失败后开启指数退避熔断。"""
        self.breaker.failures += 1
        if self.breaker.failures >= 3:
            self.breaker.open_until = now + timedelta(seconds=self.breaker.backoff_seconds)
            self.breaker.backoff_seconds = min(self.breaker.backoff_seconds * 2, 120)

    def _reset_breaker(self) -> None:
        """在收到有效行情后重置连续失败与退避状态。"""
        self.breaker.failures = 0
        self.breaker.open_until = None
        self.breaker.backoff_seconds = 30

    @staticmethod
    def _provider_supports(provider: QuoteProvider, symbol: str) -> bool:
        """判断行情源是否声明支持指定证券，未声明时保持兼容。"""
        supports = getattr(provider, "supports_symbol", None)
        return True if supports is None else bool(supports(symbol))

    async def refresh(self, session: Session, securities: Sequence[Security], *, now: datetime | None = None) -> list[QuoteSnapshot]:
        """串行刷新有效行情，并为缺失或失败的证券保留过期缓存。"""
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        async with self.lock:
            if (
                self.last_refresh_at is not None
                and current - self.last_refresh_at
                < timedelta(seconds=self.min_refresh_interval_seconds)
            ):
                return self.service.read_cached(session, securities, now=current)
            if self.breaker.is_open_at(current):
                return self.service.read_cached(session, securities, now=current)
            proxy_securities = list(self.service.proxy_securities_for(session, securities).values())
            refresh_targets: list[Security] = []
            seen_ids: set[str] = set()
            for security in (*securities, *proxy_securities):
                if security.id in seen_ids:
                    continue
                seen_ids.add(security.id)
                refresh_targets.append(security)
            supported_securities = [
                security
                for security in refresh_targets
                if self._provider_supports(
                    self.provider, self.service.security_symbol(security)
                )
            ]
            requested_symbols = [
                self.service.security_symbol(security)
                for security in supported_securities
            ]
            symbols = requested_symbols
            if not symbols:
                return self.service.read_cached(session, securities, now=current)
            self.last_refresh_at = current
            try:
                # 上游线程不接触数据库；只有等待结束后，当前请求会话才写入结果。
                snapshots = await asyncio.to_thread(self.provider.get_quotes, symbols)
                requested_set = set(symbols)
                fresh = {
                    snapshot.symbol: snapshot
                    for snapshot in snapshots.values()
                    if not snapshot.is_stale and snapshot.symbol in requested_set
                }
                if fresh:
                    self._reset_breaker()
                else:
                    self._open_breaker(current)
                self.service.upsert(session, fresh.values(), now=current)
                self.service.mark_missing_stale(
                    session,
                    supported_securities,
                    received_symbols=set(fresh),
                    now=current,
                )
                session.commit()
                if fresh and self.service.hub is not None:
                    self.service.hub.publish(fresh, mark_dirty=False)
                missing_count = len(set(requested_symbols) - set(fresh))
                if missing_count:
                    logger.warning(
                        "quote refresh returned incomplete coverage",
                        extra={
                            "provider": self.provider.name,
                            "requested_count": len(requested_symbols),
                            "stale_count": missing_count,
                        },
                    )
                return self.service.read_cached(session, securities, now=current)
            except Exception as exc:
                self._open_breaker(current)
                session.rollback()
                self.service.mark_missing_stale(
                    session,
                    supported_securities,
                    received_symbols=set(),
                    now=current,
                )
                session.commit()
                logger.warning(
                    "quote refresh failed; serving stale cache",
                    extra={
                        "provider": self.provider.name,
                        "requested_count": len(requested_symbols),
                        "error_type": type(exc).__name__,
                    },
                )
                return self.service.read_cached(session, securities, now=current)

    async def collect(self, session: Session, securities: Sequence[Security], *, refresh: bool = True, now: datetime | None = None) -> list[QuoteSnapshot]:
        """按调用方选择刷新或只读取共享行情缓存。"""
        if refresh:
            return await self.refresh(session, securities, now=now)
        return self.service.read_cached(session, securities, now=now)

