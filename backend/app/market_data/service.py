from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import TargetStatus
from app.domain.precision import round_price
from app.models.ledger import Lot, QuoteCache, Security

from .calendar import AShareTradingCalendar, OpenFundCalendar, USTradingCalendar
from .provider import QuoteSnapshot, canonical_symbol


class QuoteService:
    """Persistence and domain boundary for shared quote-cache rows."""

    def __init__(self, calendar: AShareTradingCalendar | None = None) -> None:
        """初始化 A 股、美股与场外基金缓存新鲜度日历。"""
        self.calendar = calendar or AShareTradingCalendar()
        self.us_calendar = USTradingCalendar()
        self.fund_calendar = OpenFundCalendar()

    def calendar_for_security(self, security: Security) -> AShareTradingCalendar:
        """返回证券所属市场的交易日历。"""
        market = security.market.upper()
        if market == "US":
            return self.us_calendar
        if market == "OF":
            return self.fund_calendar
        return self.calendar

    @staticmethod
    def security_symbol(security: Security) -> str:
        """返回证券的规范行情代码。"""
        return canonical_symbol(f"{security.market}:{security.symbol}")

    def securities_for_symbols(self, session: Session, symbols: Sequence[str]) -> dict[str, Security]:
        """按规范代码查找数据库中已登记的证券。"""
        wanted = {canonical_symbol(value) for value in symbols}
        rows = session.scalars(select(Security)).all()
        return {self.security_symbol(row): row for row in rows if self.security_symbol(row) in wanted}

    def symbols_for_scope(
        self,
        session: Session,
        *,
        account_ids: Sequence[str] | None = None,
    ) -> dict[str, Security]:
        """返回指定账户范围（或全库）持仓和自选对应的证券映射。"""
        if account_ids is not None and len(account_ids) == 0:
            return {}
        rows = session.scalars(select(Security)).all()
        lot_stmt = select(Lot.security_id).where(Lot.status == "OPEN")
        if account_ids is not None:
            lot_stmt = lot_stmt.where(Lot.account_id.in_(account_ids))
        watch_ids = set(session.scalars(lot_stmt).all())
        # Include watchlist securities without introducing an ORM relationship
        # dependency, keeping the service compatible with the existing schema.
        from app.models.ledger import WatchlistItem

        watch_stmt = select(WatchlistItem.security_id)
        if account_ids is not None:
            watch_stmt = watch_stmt.where(WatchlistItem.account_id.in_(account_ids))
        watch_ids.update(session.scalars(watch_stmt).all())
        return {self.security_symbol(row): row for row in rows if row.id in watch_ids}

    def read_cached(self, session: Session, securities: Iterable[Security], *, now: datetime | None = None) -> list[QuoteSnapshot]:
        """读取请求证券的共享缓存，并计算缓存是否过期。"""
        current = now or datetime.now(timezone.utc)
        result: list[QuoteSnapshot] = []
        for security in securities:
            row = session.get(QuoteCache, security.id)
            if row is None:
                continue
            fetched_at = self._as_utc(row.fetched_at)
            source_time = self._as_utc(row.source_time) if row.source_time is not None else None
            stale = row.is_stale or self.calendar_for_security(security).is_stale(
                fetched_at, now=current
            )
            try:
                result.append(
                    QuoteSnapshot(
                        symbol=self.security_symbol(security),
                        last_price=row.last_price,
                        prev_close=row.prev_close,
                        change_pct=row.change_pct,
                        source_time=source_time,
                        fetched_at=fetched_at,
                        provider=row.provider,
                        is_stale=stale,
                    )
                )
            except (TypeError, ValueError):
                # 旧缓存可能来自更宽松版本；无效价格按无行情处理，不能改写成零。
                continue
        return result

    def upsert(self, session: Session, snapshots: Iterable[QuoteSnapshot], *, now: datetime | None = None) -> list[QuoteSnapshot]:
        """把有效行情写入共享缓存并评估止盈提醒。"""
        current = now or datetime.now(timezone.utc)
        result: list[QuoteSnapshot] = []
        target_snapshots: list[QuoteSnapshot] = []
        securities = {self.security_symbol(row): row for row in session.scalars(select(Security)).all()}
        for snapshot in snapshots:
            if snapshot.last_price <= 0 or snapshot.is_stale:
                continue
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
            row.source_time = self._as_utc(snapshot.source_time) if snapshot.source_time else None
            fetched_at = self._as_utc(snapshot.fetched_at or current)
            row.fetched_at = fetched_at
            row.provider = snapshot.provider
            stale = snapshot.is_stale or self.calendar_for_security(security).is_stale(
                fetched_at, now=current
            )
            row.is_stale = stale
            stored = snapshot.stale() if stale else snapshot
            result.append(stored)
            if not stale:
                target_snapshots.append(stored)
        session.flush()
        self.evaluate_targets(session, target_snapshots, at=current)
        return result

    def mark_stale(self, session: Session, securities: Iterable[Security]) -> list[QuoteSnapshot]:
        """把请求范围内的全部缓存行情标记为过期。"""
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

    def mark_missing_stale(
        self,
        session: Session,
        securities: Iterable[Security],
        *,
        received_symbols: set[str],
        now: datetime | None = None,
    ) -> list[QuoteSnapshot]:
        """将本次未收到有效行情的缓存标记为过期并返回完整缓存范围。"""
        security_list = list(securities)
        received = {canonical_symbol(symbol) for symbol in received_symbols}
        for security in security_list:
            symbol = self.security_symbol(security)
            if symbol in received:
                continue
            row = session.get(QuoteCache, security.id)
            if row is not None:
                row.is_stale = True
        session.flush()
        return self.read_cached(session, security_list, now=now)

    def evaluate_targets(self, session: Session, snapshots: Iterable[QuoteSnapshot], *, at: datetime | None = None) -> int:
        """只根据有效行情触发活动止盈提醒。"""
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

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """把数据库返回的无时区时间按 UTC 补齐时区。"""
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

