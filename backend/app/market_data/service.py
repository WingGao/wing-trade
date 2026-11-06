from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import TargetStatus
from app.domain.precision import round_price
from app.models.ledger import Lot, QuoteCache, Security

from .calendar import AShareTradingCalendar, USTradingCalendar
from .provider import QuoteSnapshot, canonical_symbol


class QuoteService:
    """Persistence and domain boundary for shared quote-cache rows."""

    def __init__(self, calendar: AShareTradingCalendar | None = None) -> None:
        self.calendar = calendar or AShareTradingCalendar()
        self.us_calendar = USTradingCalendar()

    def calendar_for_security(self, security: Security) -> AShareTradingCalendar:
        return self.us_calendar if security.market.upper() == "US" else self.calendar

    @staticmethod
    def security_symbol(security: Security) -> str:
        return canonical_symbol(f"{security.market}:{security.symbol}")

    def securities_for_symbols(self, session: Session, symbols: Sequence[str]) -> dict[str, Security]:
        wanted = {canonical_symbol(value) for value in symbols}
        rows = session.scalars(select(Security)).all()
        return {self.security_symbol(row): row for row in rows if self.security_symbol(row) in wanted}

    def symbols_for_scope(self, session: Session) -> dict[str, Security]:
        rows = session.scalars(select(Security)).all()
        watch_ids = set(session.scalars(select(Lot.security_id).where(Lot.status == "OPEN")).all())
        # Include watchlist securities without introducing an ORM relationship
        # dependency, keeping the service compatible with the existing schema.
        from app.models.ledger import WatchlistItem

        watch_ids.update(session.scalars(select(WatchlistItem.security_id)).all())
        return {self.security_symbol(row): row for row in rows if row.id in watch_ids}

    def read_cached(self, session: Session, securities: Iterable[Security], *, now: datetime | None = None) -> list[QuoteSnapshot]:
        current = now or datetime.now(timezone.utc)
        result: list[QuoteSnapshot] = []
        for security in securities:
            row = session.get(QuoteCache, security.id)
            if row is None:
                continue
            stale = row.is_stale or self.calendar_for_security(security).is_stale(row.fetched_at, now=current)
            result.append(
                QuoteSnapshot(
                    symbol=self.security_symbol(security),
                    last_price=row.last_price,
                    prev_close=row.prev_close,
                    change_pct=row.change_pct,
                    source_time=row.source_time,
                    fetched_at=row.fetched_at,
                    provider=row.provider,
                    is_stale=stale,
                )
            )
        return result

    def upsert(self, session: Session, snapshots: Iterable[QuoteSnapshot], *, now: datetime | None = None) -> list[QuoteSnapshot]:
        current = now or datetime.now(timezone.utc)
        result: list[QuoteSnapshot] = []
        securities = {self.security_symbol(row): row for row in session.scalars(select(Security)).all()}
        for snapshot in snapshots:
            security = securities.get(canonical_symbol(snapshot.symbol))
            if security is None:
                continue
            row = session.get(QuoteCache, security.id)
            if row is None:
                row = QuoteCache(security_id=security.id, last_price=snapshot.last_price, provider=snapshot.provider)
                session.add(row)
            row.last_price = round_price(snapshot.last_price)
            row.prev_close = round_price(snapshot.prev_close) if snapshot.prev_close is not None else None
            row.change_pct = round_price(snapshot.change_pct) if snapshot.change_pct is not None else None
            row.source_time = snapshot.source_time
            row.fetched_at = snapshot.fetched_at or current
            row.provider = snapshot.provider
            row.is_stale = snapshot.is_stale
            result.append(snapshot)
        session.flush()
        self.evaluate_targets(session, result, at=current)
        return result

    def mark_stale(self, session: Session, securities: Iterable[Security]) -> list[QuoteSnapshot]:
        securities = list(securities)
        snapshots: list[QuoteSnapshot] = []
        for snapshot in self.read_cached(session, securities):
            security = next((item for item in securities if self.security_symbol(item) == snapshot.symbol), None)
            row = session.get(QuoteCache, security.id) if security is not None else None
            if row is not None:
                row.is_stale = True
            snapshots.append(snapshot.stale())
        session.flush()
        return snapshots

    def evaluate_targets(self, session: Session, snapshots: Iterable[QuoteSnapshot], *, at: datetime | None = None) -> int:
        current = at or datetime.now(timezone.utc)
        by_symbol = {canonical_symbol(snapshot.symbol): snapshot for snapshot in snapshots}
        triggered = 0
        for lot in session.scalars(select(Lot).where(Lot.status == "OPEN")).all():
            security = session.get(Security, lot.security_id)
            if security is None:
                continue
            snapshot = by_symbol.get(self.security_symbol(security))
            if snapshot is None or snapshot.is_stale:
                continue
            for target in lot.targets:
                if target.status != TargetStatus.ACTIVE.value or not target.alert_enabled or target.target_price_estimate is None:
                    continue
                if snapshot.last_price >= target.target_price_estimate:
                    target.status = TargetStatus.TRIGGERED.value
                    target.triggered_at = current
                    triggered += 1
        if triggered:
            session.flush()
        return triggered

