"""从不可变交易事实重建单个持仓范围的批次、配对和周期。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.enums import MatchingMethod
from app.domain.exceptions import InsufficientLotQtyError
from app.domain.precision import round_amount, round_price, round_qty
from app.models.ledger import Cycle, Lot, ProfitTarget, Trade, TradeMatch
from app.services.ledger import TradeLedgerService
from app.services.matching import MatchingService
from app.services.position_refresh import PositionRefreshService


def _utc(value: datetime) -> datetime:
    """统一 SQLite 无时区与 API 有时区时间，保证重放顺序确定。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _derived_id(value: str) -> str:
    """根据事实锚点生成稳定的派生编号，重复重建不会更换实体编号。"""
    return str(uuid5(NAMESPACE_URL, f"wing-trade:recalculate:{value}"))


class RecalculationService:
    """调用方持有写事务；异常交由调用方整笔回滚，不提交中间删除。"""

    def __init__(self, session: Session, *, actor: str | None = None) -> None:
        """保留事务会话及统一审计写入器。"""
        self.session = session
        self.audit = TradeLedgerService(session, actor=actor)

    def rebuild(self, *, account_id: str, security_id: str, strategy_id: str) -> dict[str, int]:
        """重建给定范围并原子清除待刷新标记，保留交易事实与显式批次设置。"""
        session = self.session
        scope = (account_id, security_id, strategy_id)
        trades = list(
            session.scalars(
                select(Trade).where(
                    Trade.account_id == account_id,
                    Trade.security_id == security_id,
                    Trade.strategy_id == strategy_id,
                )
            )
        )
        old_lots = list(
            session.scalars(
                select(Lot).where(
                    Lot.account_id == account_id,
                    Lot.security_id == security_id,
                    Lot.strategy_id == strategy_id,
                )
            )
        )
        old_cycles = list(
            session.scalars(
                select(Cycle).where(
                    Cycle.account_id == account_id,
                    Cycle.security_id == security_id,
                    Cycle.strategy_id == strategy_id,
                )
            )
        )
        lot_by_buy = {lot.buy_trade_id: (lot.id, lot.created_at) for lot in old_lots}
        before_lots = {lot.id: self.audit._lot_snapshot(lot) for lot in old_lots}
        before_cycles = {cycle.id: self.audit._cycle_snapshot(cycle) for cycle in old_cycles}
        target_values = [
            {column.name: getattr(target, column.name) for column in ProfitTarget.__table__.columns}
            for lot in old_lots
            for target in lot.targets
        ]
        old_lot_ids = list(before_lots)
        trade_ids = [trade.id for trade in trades]
        # 删除顺序遵循 lot 的 RESTRICT 外键；所有操作仍处于同一写事务。
        session.execute(
            delete(TradeMatch).where(
                TradeMatch.lot_id.in_(old_lot_ids) | TradeMatch.sell_trade_id.in_(trade_ids)
            ),
            execution_options={"synchronize_session": "fetch"},
        )
        session.execute(
            delete(ProfitTarget).where(ProfitTarget.lot_id.in_(old_lot_ids)),
            execution_options={"synchronize_session": "fetch"},
        )
        session.execute(
            delete(Lot).where(Lot.id.in_(old_lot_ids)),
            execution_options={"synchronize_session": "fetch"},
        )
        original_trade_cycles = {trade.id: trade.cycle_id for trade in trades}
        original_updated = {trade.id: trade.updated_at for trade in trades}
        for trade in trades:
            # cycle_id 属于派生关系，原始成交、幂等键、状态和更新时间保持原样。
            trade.cycle_id = None
            trade.updated_at = original_updated[trade.id]
            session.expire(trade, ["lot", "matches", "cycle"])
        session.flush()
        session.execute(
            delete(Cycle).where(Cycle.id.in_(list(before_cycles))),
            execution_options={"synchronize_session": "fetch"},
        )
        session.flush()

        active = sorted(
            (trade for trade in trades if trade.status == "ACTIVE"),
            key=lambda trade: (
                _utc(trade.executed_at),
                0 if trade.side == "BUY" else 1,
                _utc(trade.created_at),
                trade.id,
            ),
        )
        current: Cycle | None = None
        rebuilt: list[Lot] = []
        new_cycles: list[Cycle] = []
        match_count = 0
        for trade in active:
            if trade.side == "BUY":
                if current is None:
                    current = Cycle(
                        id=_derived_id(f"cycle:{':'.join(scope)}:{trade.id}"),
                        account_id=account_id,
                        security_id=security_id,
                        strategy_id=strategy_id,
                        opened_at=trade.executed_at,
                        status="OPEN",
                    )
                    session.add(current)
                    session.flush()
                    new_cycles.append(current)
                quantity = round_qty(trade.qty)
                cost = round_amount(trade.gross_amount + trade.fees_total)
                old = lot_by_buy.get(trade.id)
                lot = Lot(
                    id=old[0] if old else _derived_id(f"lot:{trade.id}"),
                    buy_trade_id=trade.id,
                    account_id=account_id,
                    security_id=security_id,
                    strategy_id=strategy_id,
                    cycle_id=current.id,
                    original_qty=quantity,
                    original_cost=cost,
                    remaining_qty=quantity,
                    remaining_cost=cost,
                    unit_cost=round_price(cost / quantity),
                    executed_at=trade.executed_at,
                    created_at=old[1] if old else trade.created_at,
                    status="OPEN",
                )
                session.add(lot)
                session.flush()
                rebuilt.append(lot)
            elif trade.side == "SELL":
                if current is None:
                    raise InsufficientLotQtyError("交易顺序中的卖出没有可用买入，重建已回滚")
                candidates = [lot for lot in rebuilt if lot.cycle_id == current.id]
                requests = MatchingService.auto_preview(
                    candidates,
                    trade.qty,
                    method=MatchingMethod.EQUAL,
                    sell_executed_at=trade.executed_at,
                    account_id=account_id,
                    security_id=security_id,
                    strategy_id=strategy_id,
                )
                allocations = MatchingService.allocate(
                    {lot.id: lot for lot in candidates},
                    requests,
                    sell_qty=trade.qty,
                    sell_cash_in=round_amount(trade.gross_amount - trade.fees_total),
                    method=MatchingMethod.EQUAL,
                    sell_executed_at=trade.executed_at,
                    account_id=account_id,
                    security_id=security_id,
                    strategy_id=strategy_id,
                )
                for allocation in allocations:
                    session.add(
                        TradeMatch(
                            id=_derived_id(f"match:{trade.id}:{allocation.lot_id}"),
                            sell_trade_id=trade.id,
                            lot_id=allocation.lot_id,
                            matched_qty=allocation.matched_qty,
                            allocated_buy_cost=allocation.allocated_buy_cost,
                            allocated_sell_net=allocation.allocated_sell_net,
                            realized_pnl=allocation.realized_pnl,
                            matching_method=MatchingMethod.EQUAL.value,
                            created_at=trade.created_at,
                        )
                    )
                    match_count += 1
            if current is not None:
                trade.cycle_id = current.id
                if not any(lot.remaining_qty > 0 for lot in rebuilt if lot.cycle_id == current.id):
                    current.status = "CLOSED"
                    current.closed_at = trade.executed_at
                    current = None
            session.flush()

        restored_ids = {lot.id for lot in rebuilt}
        for values in target_values:
            if values["lot_id"] in restored_ids:
                session.add(ProfitTarget(**values))
        for lot in rebuilt:
            self.audit._audit(
                "LOT",
                lot.id,
                "LOT_RECALCULATED",
                before=before_lots.get(lot.id),
                after=self.audit._lot_snapshot(lot),
                reason="从有效交易按等量策略重建",
            )
        for lot_id in set(before_lots) - restored_ids:
            self.audit._audit(
                "LOT",
                lot_id,
                "LOT_REMOVED_BY_RECALCULATION",
                before=before_lots[lot_id],
                reason="买入交易已作废",
            )
        for cycle in new_cycles:
            self.audit._audit(
                "CYCLE", cycle.id, "CYCLE_RECALCULATED", after=self.audit._cycle_snapshot(cycle)
            )
        for cycle_id, before in before_cycles.items():
            if cycle_id not in {cycle.id for cycle in new_cycles}:
                self.audit._audit(
                    "CYCLE", cycle_id, "CYCLE_REMOVED_BY_RECALCULATION", before=before
                )
        for trade in trades:
            # 派生关系变动不应伪造一次事实编辑，恢复原事实更新时间。
            trade.updated_at = original_updated[trade.id]
            if original_trade_cycles[trade.id] != trade.cycle_id:
                self.audit._audit(
                    "TRADE",
                    trade.id,
                    "TRADE_CYCLE_RECALCULATED",
                    before={"cycle_id": original_trade_cycles[trade.id]},
                    after={"cycle_id": trade.cycle_id},
                )
        PositionRefreshService.clear(
            session, account_id=account_id, security_id=security_id, strategy_id=strategy_id
        )
        session.flush()
        return {"rebuiltLots": len(rebuilt), "rebuiltMatches": match_count}
