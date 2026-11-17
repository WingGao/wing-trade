from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, ExactDecimal
from app.domain.enums import CalcBasis, CycleStatus, LotStatus, TargetStatus, TradeSide, TradeSource, TradeStatus


def _uuid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    broker: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    strategies: Mapped[list[Strategy]] = relationship(back_populates="account", cascade="all, delete-orphan")
    trades: Mapped[list[Trade]] = relationship(back_populates="account")


class Security(Base):
    __tablename__ = "securities"
    __table_args__ = (UniqueConstraint("market", "symbol", name="uq_securities_market_symbol"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(128))
    security_type: Mapped[str] = mapped_column(String(32), nullable=False, default="STOCK")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("account_id", "security_id", name="uq_watchlist_account_security"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    security_id: Mapped[str] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Strategy(Base):
    __tablename__ = "strategies"
    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_strategies_account_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="DEFAULT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    account: Mapped[Account] = relationship(back_populates="strategies")


class Cycle(Base):
    __tablename__ = "cycles"
    __table_args__ = (
        Index("ix_cycles_scope_status", "account_id", "security_id", "strategy_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    security_id: Mapped[str] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CycleStatus.OPEN.value)

    trades: Mapped[list[Trade]] = relationship(back_populates="cycle")
    lots: Mapped[list[Lot]] = relationship(back_populates="cycle")


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("account_id", "client_request_id", name="uq_trades_account_client_request"),
        Index("ix_trades_scope_executed", "account_id", "security_id", "strategy_id", "executed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    security_id: Mapped[str] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
    cycle_id: Mapped[str | None] = mapped_column(ForeignKey("cycles.id", ondelete="SET NULL"))
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    qty: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    price: Mapped[Decimal] = mapped_column(ExactDecimal(6), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    commission: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False, default=Decimal("0.0000"))
    stamp_duty: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False, default=Decimal("0.0000"))
    transfer_fee: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False, default=Decimal("0.0000"))
    other_fee: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False, default=Decimal("0.0000"))
    fees_total: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False, default=Decimal("0.0000"))
    external_trade_id: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=TradeSource.MANUAL.value)
    import_batch_id: Mapped[str | None] = mapped_column(String(128))
    memo: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TradeStatus.ACTIVE.value)
    client_request_id: Mapped[str | None] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    account: Mapped[Account] = relationship(back_populates="trades")
    cycle: Mapped[Cycle | None] = relationship(back_populates="trades")
    lot: Mapped[Lot | None] = relationship(back_populates="buy_trade", uselist=False)
    matches: Mapped[list[TradeMatch]] = relationship(
        back_populates="sell_trade", foreign_keys="TradeMatch.sell_trade_id", cascade="all, delete-orphan"
    )


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (
        UniqueConstraint("buy_trade_id", name="uq_lots_buy_trade"),
        Index("ix_lots_scope_status", "account_id", "security_id", "strategy_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    buy_trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id", ondelete="RESTRICT"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    security_id: Mapped[str] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("cycles.id", ondelete="CASCADE"), nullable=False)
    original_qty: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    original_cost: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    remaining_qty: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    remaining_cost: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(ExactDecimal(6), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=LotStatus.OPEN.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    buy_trade: Mapped[Trade] = relationship(back_populates="lot", foreign_keys=[buy_trade_id])
    cycle: Mapped[Cycle] = relationship(back_populates="lots")
    matches: Mapped[list[TradeMatch]] = relationship(back_populates="lot", cascade="all, delete-orphan")
    targets: Mapped[list[ProfitTarget]] = relationship(back_populates="lot", cascade="all, delete-orphan")


class TradeMatch(Base):
    __tablename__ = "trade_matches"
    __table_args__ = (
        UniqueConstraint("sell_trade_id", "lot_id", name="uq_trade_matches_sell_lot"),
        Index("ix_trade_matches_sell", "sell_trade_id"),
        Index("ix_trade_matches_lot", "lot_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sell_trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False)
    matched_qty: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    allocated_buy_cost: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    allocated_sell_net: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    matching_method: Mapped[str] = mapped_column(String(16), nullable=False, default="FIFO")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    sell_trade: Mapped[Trade] = relationship(back_populates="matches", foreign_keys=[sell_trade_id])
    lot: Mapped[Lot] = relationship(back_populates="matches")


class ProfitTarget(Base):
    __tablename__ = "profit_targets"
    __table_args__ = (Index("ix_profit_targets_lot_status", "lot_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"), nullable=False)
    target_return_pct: Mapped[Decimal] = mapped_column(ExactDecimal(6), nullable=False)
    planned_sell_qty: Mapped[Decimal] = mapped_column(ExactDecimal(4), nullable=False)
    calculation_basis: Mapped[str] = mapped_column(String(8), nullable=False, default=CalcBasis.NET.value)
    target_price_estimate: Mapped[Decimal | None] = mapped_column(ExactDecimal(6))
    alert_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TargetStatus.ACTIVE.value)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    lot: Mapped[Lot] = relationship(back_populates="targets")


class ProfitSetting(Base):
    """全局单例或单个证券的止盈默认值，不为继承批次复制记录。"""

    __tablename__ = "profit_settings"
    __table_args__ = (
        UniqueConstraint("security_id", name="uq_profit_settings_security"),
    )

    scope_key: Mapped[str] = mapped_column(String(48), primary_key=True)
    security_id: Mapped[str | None] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE")
    )
    target_return_pct: Mapped[Decimal] = mapped_column(ExactDecimal(6), nullable=False)
    calculation_basis: Mapped[str] = mapped_column(String(8), nullable=False, default="NET")
    alert_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class QuoteCache(Base):
    __tablename__ = "quote_cache"

    security_id: Mapped[str] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), primary_key=True)
    last_price: Mapped[Decimal] = mapped_column(ExactDecimal(6), nullable=False)
    prev_close: Mapped[Decimal | None] = mapped_column(ExactDecimal(6))
    change_pct: Mapped[Decimal | None] = mapped_column(ExactDecimal(6))
    source_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    is_stale: Mapped[bool] = mapped_column(default=False, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_entity", "entity_type", "entity_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


__all__ = [
    "Base",
    "Account",
    "Security",
    "WatchlistItem",
    "Strategy",
    "Cycle",
    "Trade",
    "Lot",
    "TradeMatch",
    "ProfitTarget",
    "QuoteCache",
    "AuditEvent",
]
