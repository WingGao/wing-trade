from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import TargetStatus
from app.domain.precision import round_price
from app.models.ledger import Lot, QuoteCache, Security

from .calendar import AShareTradingCalendar, OpenFundCalendar, USTradingCalendar
from .provider import QuoteSnapshot, canonical_symbol


class QuoteService:
    """Persistence and domain boundary for shared quote-cache rows."""

    def __init__(self, calendar: AShareTradingCalendar | None = None, *, hub: Any | None = None) -> None:
        """初始化 A 股、美股与场外基金缓存新鲜度日历及可选内存行情中心引用。"""
        self.calendar = calendar or AShareTradingCalendar()
        self.us_calendar = USTradingCalendar()
        self.fund_calendar = OpenFundCalendar()
        self.hub: Any | None = hub

    def bind_hub(self, hub: Any | None) -> None:
        """绑定或解除进程级内存行情中心，使缓存读取优先命中内存最新快照。"""
        self.hub = hub

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

    def proxy_securities_for(
        self,
        session: Session,
        securities: Iterable[Security],
    ) -> dict[str, Security]:
        """提取已绑定跟踪参考标的的场外基金所对应的参考证券映射。"""
        proxy_symbols = {
            canonical_symbol(security.proxy_symbol)
            for security in securities
            if security.market.upper() == "OF" and security.proxy_symbol
        }
        if not proxy_symbols:
            return {}
        return self.securities_for_symbols(session, list(proxy_symbols))

    def _read_raw_cached(
        self,
        session: Session,
        security: Security,
        *,
        now: datetime,
        prefer_hub: bool = True,
    ) -> QuoteSnapshot | None:
        """读取单只证券的原始行情快照（优先合并内存行情中心与 quote_cache 中的最新数据）。"""
        symbol = self.security_symbol(security)
        hub_snap: QuoteSnapshot | None = None
        if prefer_hub and self.hub is not None:
            hub_snap = self.hub.get_raw_quote(symbol, now=now)

        row = session.get(QuoteCache, security.id)
        db_snap: QuoteSnapshot | None = None
        if row is not None:
            fetched_at = self._as_utc(row.fetched_at)
            source_time = self._as_utc(row.source_time) if row.source_time is not None else None
            calendar = self.calendar_for_security(security)
            stale = row.is_stale or calendar.is_stale(fetched_at, now=now)
            market_session = (
                None
                if security.market.upper() == "OF"
                else calendar.session_phase(source_time or fetched_at)
            )
            try:
                db_snap = QuoteSnapshot(
                    symbol=symbol,
                    last_price=row.last_price,
                    prev_close=row.prev_close,
                    change_pct=row.change_pct,
                    source_time=source_time,
                    fetched_at=fetched_at,
                    provider=row.provider,
                    is_stale=stale,
                    market_session=market_session,
                )
            except (TypeError, ValueError):
                # 旧缓存可能来自更宽松版本；无效价格按无行情处理，不能改写成零。
                db_snap = None

        if hub_snap is None:
            return db_snap
        if db_snap is None:
            return hub_snap
        if self._as_utc(hub_snap.fetched_at) >= self._as_utc(db_snap.fetched_at):
            return hub_snap
        return db_snap

    def resolve_effective_snapshot(
        self,
        security: Security,
        snapshot: QuoteSnapshot | None,
        proxy_snapshot: QuoteSnapshot | None,
    ) -> QuoteSnapshot | None:
        """为已绑定参考标的的场外基金按交易日期对齐规则派生盘中估算净值。"""
        if snapshot is None:
            return None
        if security.market.upper() != "OF" or not security.proxy_symbol:
            return snapshot

        proxy_symbol = canonical_symbol(security.proxy_symbol)
        proxy_name = security.proxy_name
        tracking_ratio = round_price(security.tracking_ratio or Decimal("0.950000"))
        nav_date = (
            snapshot.source_time.astimezone(self.calendar.timezone).date()
            if snapshot.source_time is not None
            else None
        )
        nav_date_str = nav_date.isoformat() if nav_date is not None else None

        def _official_with_meta() -> QuoteSnapshot:
            """返回官方净值快照并附带参考标的绑定元数据。"""
            return QuoteSnapshot(
                symbol=snapshot.symbol,
                last_price=snapshot.last_price,
                prev_close=snapshot.prev_close,
                change_pct=snapshot.change_pct,
                source_time=snapshot.source_time,
                fetched_at=snapshot.fetched_at,
                provider=snapshot.provider,
                is_stale=snapshot.is_stale,
                market_session=snapshot.market_session,
                is_estimated=False,
                official_nav=snapshot.last_price,
                official_nav_date=nav_date_str,
                proxy_symbol=proxy_symbol,
                proxy_name=proxy_name,
                proxy_change_pct=proxy_snapshot.change_pct if proxy_snapshot is not None else None,
                tracking_ratio=tracking_ratio,
            )

        if (
            nav_date is None
            or proxy_snapshot is None
            or proxy_snapshot.change_pct is None
        ):
            return _official_with_meta()

        proxy_time = proxy_snapshot.source_time or proxy_snapshot.fetched_at
        proxy_date = proxy_time.astimezone(self.calendar.timezone).date()
        # 若官方净值已更新至参考标的最新交易日，自动切回官方净值，防止重复叠乘涨跌幅。
        if nav_date >= proxy_date:
            return _official_with_meta()

        estimated_change_pct = round_price(proxy_snapshot.change_pct * tracking_ratio)
        estimated_price = round_price(
            snapshot.last_price * (Decimal("1") + estimated_change_pct / Decimal("100"))
        )
        if estimated_price <= 0:
            return _official_with_meta()

        return QuoteSnapshot(
            symbol=snapshot.symbol,
            last_price=estimated_price,
            prev_close=snapshot.last_price,
            change_pct=estimated_change_pct,
            source_time=proxy_snapshot.source_time,
            fetched_at=proxy_snapshot.fetched_at,
            provider=f"{snapshot.provider}+proxy",
            is_stale=proxy_snapshot.is_stale,
            market_session=proxy_snapshot.market_session,
            is_estimated=True,
            official_nav=snapshot.last_price,
            official_nav_date=nav_date_str,
            proxy_symbol=proxy_symbol,
            proxy_name=proxy_name,
            proxy_change_pct=proxy_snapshot.change_pct,
            tracking_ratio=tracking_ratio,
        )

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

    def read_cached(
        self,
        session: Session,
        securities: Iterable[Security],
        *,
        now: datetime | None = None,
        apply_proxy_estimate: bool = True,
    ) -> list[QuoteSnapshot]:
        """读取请求证券的共享缓存，并按需为已绑定参考标的的场外基金派生盘中估算净值。"""
        current = now or datetime.now(timezone.utc)
        security_list = list(securities)
        proxy_snapshots: dict[str, QuoteSnapshot] = {}
        if apply_proxy_estimate:
            proxy_map = self.proxy_securities_for(session, security_list)
            for proxy_symbol, proxy_security in proxy_map.items():
                proxy_snap = self._read_raw_cached(session, proxy_security, now=current)
                if proxy_snap is not None:
                    proxy_snapshots[proxy_symbol] = proxy_snap

        result: list[QuoteSnapshot] = []
        for security in security_list:
            raw_snapshot = self._read_raw_cached(session, security, now=current)
            if raw_snapshot is None:
                continue
            if apply_proxy_estimate and security.market.upper() == "OF" and security.proxy_symbol:
                proxy_snap = proxy_snapshots.get(canonical_symbol(security.proxy_symbol))
                effective = self.resolve_effective_snapshot(security, raw_snapshot, proxy_snap)
                if effective is not None:
                    result.append(effective)
            else:
                result.append(raw_snapshot)
        return result

    def upsert(
        self,
        session: Session,
        snapshots: Iterable[QuoteSnapshot],
        *,
        now: datetime | None = None,
        evaluate_targets: bool = True,
    ) -> list[QuoteSnapshot]:
        """把有效行情写入共享缓存，并按需评估止盈提醒。"""
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
        if evaluate_targets:
            self.evaluate_targets(session, target_snapshots, at=current)
        return result

    def mark_stale(self, session: Session, securities: Iterable[Security]) -> list[QuoteSnapshot]:
        """把请求范围内的全部缓存行情标记为过期。"""
        securities = list(securities)
        snapshots: list[QuoteSnapshot] = []
        for snapshot in self.read_cached(session, securities, apply_proxy_estimate=False):
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

