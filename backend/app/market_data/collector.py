from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy.orm import Session

from app.models.ledger import Security

from .calendar import AShareTradingCalendar
from .provider import QuoteProvider, QuoteSnapshot
from .service import QuoteService


@dataclass(slots=True)
class CircuitBreakerState:
    failures: int = 0
    open_until: datetime | None = None
    backoff_seconds: int = 30

    @property
    def is_open(self) -> bool:
        return self.open_until is not None and datetime.now(timezone.utc) < self.open_until


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
        self.provider = provider
        self.service = service or QuoteService(calendar)
        self.calendar = calendar or self.service.calendar
        self.min_refresh_interval_seconds = min_refresh_interval_seconds
        self.last_refresh_at: datetime | None = None
        self.lock = asyncio.Lock()
        self.breaker = CircuitBreakerState()

    def _open_breaker(self, now: datetime) -> None:
        self.breaker.failures += 1
        if self.breaker.failures >= 3:
            self.breaker.open_until = now + timedelta(seconds=self.breaker.backoff_seconds)
            self.breaker.backoff_seconds = min(self.breaker.backoff_seconds * 2, 120)

    def _reset_breaker(self) -> None:
        self.breaker.failures = 0
        self.breaker.open_until = None
        self.breaker.backoff_seconds = 30

    async def refresh(self, session: Session, securities: Sequence[Security], *, now: datetime | None = None) -> list[QuoteSnapshot]:
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
            if self.breaker.is_open:
                return self.service.read_cached(session, securities, now=current)
            symbols = [self.service.security_symbol(security) for security in securities]
            self.last_refresh_at = current
            try:
                snapshots = await asyncio.to_thread(self.provider.get_quotes, symbols)
                self._reset_breaker()
                result = self.service.upsert(session, snapshots.values(), now=current)
                session.commit()
                return result
            except Exception:
                self._open_breaker(current)
                session.rollback()
                stale = self.service.read_cached(session, securities, now=current)
                for snapshot in stale:
                    row_security = next((security for security in securities if self.service.security_symbol(security) == snapshot.symbol), None)
                    if row_security is not None:
                        from app.models.ledger import QuoteCache

                        row = session.get(QuoteCache, row_security.id)
                        if row is not None:
                            row.is_stale = True
                session.commit()
                return [snapshot.stale() for snapshot in stale]

    async def collect(self, session: Session, securities: Sequence[Security], *, refresh: bool = True, now: datetime | None = None) -> list[QuoteSnapshot]:
        if refresh:
            return await self.refresh(session, securities, now=now)
        return self.service.read_cached(session, securities, now=now)

