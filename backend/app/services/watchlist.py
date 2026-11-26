"""关注证券管理与首页混入视图只读聚合服务。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import TradeSide, TradeStatus
from app.domain.exceptions import EntityNotFoundError, ValidationError
from app.domain.markets import currency_for_market
from app.domain.precision import round_amount, round_price
from app.domain.time import format_beijing_iso
from app.market_data.amplitude import DailyAmplitudeService
from app.market_data.provider import QuoteSnapshot
from app.market_data.service import QuoteService
from app.models.ledger import Lot, Security, Strategy, Trade, TradeMatch, WatchlistItem, utcnow
from app.services.accounting import AccountingService


def _fmt_decimal(value: Decimal | None, scale: int = 6) -> str | None:
    """将高精度十进制数格式化为固定小数位字符串，空值返回 None。"""
    return None if value is None else f"{value:.{scale}f}"


def _as_utc(value: datetime) -> datetime:
    """将时间规整为带 UTC 时区的时间对象，避免比较异常。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _normalize_note(note: str | None) -> str | None:
    """清理关注备注首尾空白，纯空白视为 None。"""
    if note is None:
        return None
    cleaned = note.strip()
    return cleaned or None


def _validate_target_and_alert(
    *,
    target_buy_price: Decimal | None,
    alert_enabled: bool,
) -> tuple[Decimal | None, bool]:
    """校验目标买回价与提醒开关组合规则。"""
    normalized_price: Decimal | None = None
    if target_buy_price is not None:
        normalized_price = round_price(target_buy_price)
        if normalized_price <= 0:
            raise ValidationError("目标买回价必须大于 0")
    if alert_enabled and normalized_price is None:
        raise ValidationError("开启买回价提醒前请先设置有效的目标买回价")
    return normalized_price, bool(alert_enabled)


def _quote_dto(security: Security, snapshot: QuoteSnapshot | None) -> dict[str, Any] | None:
    """将统一行情快照序列化为与首页一致的 camelCase DTO。"""
    if snapshot is None:
        return None
    return {
        "symbol": QuoteService.security_symbol(security),
        "securityId": security.id,
        "market": security.market,
        "currency": currency_for_market(security.market),
        "lastPrice": _fmt_decimal(snapshot.last_price, 6),
        "prevClose": _fmt_decimal(snapshot.prev_close, 6),
        "changePct": _fmt_decimal(snapshot.change_pct, 6),
        "sourceTime": format_beijing_iso(snapshot.source_time),
        "fetchedAt": format_beijing_iso(snapshot.fetched_at),
        "provider": snapshot.provider,
        "isStale": bool(snapshot.is_stale),
        "marketSession": snapshot.market_session,
        "isEstimated": bool(snapshot.is_estimated),
        "officialNav": _fmt_decimal(snapshot.official_nav, 6),
        "officialNavDate": snapshot.official_nav_date,
        "proxySymbol": snapshot.proxy_symbol,
        "proxyName": snapshot.proxy_name,
        "proxyChangePct": _fmt_decimal(snapshot.proxy_change_pct, 6),
        "trackingRatio": _fmt_decimal(snapshot.tracking_ratio, 6),
    }


def _security_dto(security: Security) -> dict[str, Any]:
    """序列化证券基础元数据。"""
    return {
        "id": security.id,
        "market": security.market,
        "symbol": security.symbol,
        "name": security.name,
        "securityType": security.security_type,
        "status": security.status,
        "currency": currency_for_market(security.market),
        "proxySymbol": security.proxy_symbol,
        "proxyName": security.proxy_name,
        "trackingRatio": _fmt_decimal(security.tracking_ratio, 6),
    }


class WatchlistService:
    """封装关注列表的增改删与带行情/历史参考指标的只读聚合投影。"""

    @staticmethod
    def upsert(
        session: Session,
        *,
        account_id: str,
        security_id: str,
        sort_order: int = 0,
        target_buy_price: Decimal | None = None,
        alert_enabled: bool = False,
        note: str | None = None,
        target_buy_price_provided: bool = False,
        alert_enabled_provided: bool = False,
        note_provided: bool = False,
    ) -> WatchlistItem:
        """新增或幂等更新指定账户与证券的关注记录（在调用方写事务内执行）。"""
        item = session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.account_id == account_id,
                WatchlistItem.security_id == security_id,
            )
        )
        now = utcnow()
        if item is None:
            price, enabled = _validate_target_and_alert(
                target_buy_price=target_buy_price,
                alert_enabled=alert_enabled,
            )
            item = WatchlistItem(
                account_id=account_id,
                security_id=security_id,
                sort_order=sort_order,
                target_buy_price=price,
                alert_enabled=enabled,
                note=_normalize_note(note),
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            session.flush()
            return item

        next_price = target_buy_price if target_buy_price_provided else item.target_buy_price
        if target_buy_price_provided and target_buy_price is None and not alert_enabled_provided:
            next_enabled = False
        else:
            next_enabled = alert_enabled if alert_enabled_provided else item.alert_enabled
        price, enabled = _validate_target_and_alert(
            target_buy_price=next_price,
            alert_enabled=next_enabled,
        )
        item.target_buy_price = price
        item.alert_enabled = enabled
        if note_provided:
            item.note = _normalize_note(note)
        item.sort_order = sort_order
        item.updated_at = now
        session.flush()
        return item

    @staticmethod
    def update(
        session: Session,
        *,
        account_id: str,
        security_id: str,
        fields_set: set[str],
        sort_order: int | None = None,
        target_buy_price: Decimal | None = None,
        alert_enabled: bool | None = None,
        note: str | None = None,
    ) -> WatchlistItem:
        """局部更新已存在的关注记录，按 fields_set 精确区分未传与显式清空。"""
        item = session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.account_id == account_id,
                WatchlistItem.security_id == security_id,
            )
        )
        if item is None:
            raise EntityNotFoundError("未找到该证券的关注记录")

        price_provided = "target_buy_price" in fields_set
        alert_provided = "alert_enabled" in fields_set
        note_provided = "note" in fields_set
        sort_provided = "sort_order" in fields_set and sort_order is not None

        next_price = target_buy_price if price_provided else item.target_buy_price
        if price_provided and target_buy_price is None and not alert_provided:
            next_enabled = False
        elif alert_provided and alert_enabled is not None:
            next_enabled = alert_enabled
        else:
            next_enabled = item.alert_enabled

        validated_price, validated_enabled = _validate_target_and_alert(
            target_buy_price=next_price,
            alert_enabled=next_enabled,
        )
        item.target_buy_price = validated_price
        item.alert_enabled = validated_enabled
        if note_provided:
            item.note = _normalize_note(note)
        if sort_provided and sort_order is not None:
            item.sort_order = sort_order
        item.updated_at = utcnow()
        session.flush()
        return item

    @staticmethod
    async def list_enriched(
        session: Session,
        *,
        account_id: str,
        quote_service: QuoteService,
        amplitude_service: DailyAmplitudeService | None = None,
        security_ids_filter: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """返回账户关注列表，并聚合行情、上次卖出价、目标买回价偏离与历史已实现收益。"""
        current = _as_utc(now or datetime.now(UTC))
        stmt = (
            select(WatchlistItem, Security)
            .join(Security, Security.id == WatchlistItem.security_id)
            .where(WatchlistItem.account_id == account_id)
            .order_by(WatchlistItem.sort_order, WatchlistItem.created_at, WatchlistItem.id)
        )
        if security_ids_filter is not None:
            ids = list(security_ids_filter)
            if not ids:
                return []
            stmt = stmt.where(WatchlistItem.security_id.in_(ids))
        rows = list(session.execute(stmt).all())
        if not rows:
            return []

        securities = [security for _, security in rows]
        sec_ids = {security.id for security in securities}

        # 读取统一行情缓存（非阻塞）
        snapshots = quote_service.read_cached(session, securities, now=current)
        snapshots_by_symbol = {snap.symbol: snap for snap in snapshots}

        # 读取缓存的 20 日平均振幅（不触发阻塞网络抓取）
        amplitudes_by_security: dict[str, Any] = {}
        if amplitude_service is not None:
            amplitudes_by_security = await amplitude_service.get_or_refresh(
                session,
                securities,
                allow_refresh=False,
                now=current,
            )

        # 查询账户默认策略兜底，以及各证券是否有未结清批次、历史策略与已实现收益
        default_strategy = session.scalar(
            select(Strategy)
            .where(Strategy.account_id == account_id)
            .order_by(Strategy.created_at, Strategy.id)
        )
        default_strategy_id = default_strategy.id if default_strategy is not None else ""

        lots = list(
            session.scalars(
                select(Lot).where(
                    Lot.account_id == account_id,
                    Lot.security_id.in_(sec_ids),
                )
            ).all()
        )
        open_security_ids: set[str] = set()
        strategy_by_security: dict[str, str] = {}
        sorted_lots = sorted(
            lots, key=lambda item: (_as_utc(item.executed_at), item.id), reverse=True
        )
        for lot in sorted_lots:
            if lot.security_id not in strategy_by_security:
                strategy_by_security[lot.security_id] = lot.strategy_id
            if lot.status == "OPEN" and lot.remaining_qty > 0:
                open_security_ids.add(lot.security_id)
                strategy_by_security[lot.security_id] = lot.strategy_id

        matches = list(
            session.scalars(
                select(TradeMatch)
                .join(Lot, Lot.id == TradeMatch.lot_id)
                .where(
                    Lot.account_id == account_id,
                    Lot.security_id.in_(sec_ids),
                )
            ).all()
        )
        realized_by_security: dict[str, Decimal] = {}
        for match in matches:
            if match.sell_trade is not None and match.sell_trade.status != TradeStatus.ACTIVE.value:
                continue
            lot_sec_id = match.lot.security_id if match.lot is not None else None
            if lot_sec_id is None:
                continue
            realized_by_security[lot_sec_id] = (
                realized_by_security.get(lot_sec_id, Decimal("0")) + match.realized_pnl
            )

        sell_trades = list(
            session.scalars(
                select(Trade)
                .where(
                    Trade.account_id == account_id,
                    Trade.security_id.in_(sec_ids),
                    Trade.side == TradeSide.SELL.value,
                    Trade.status == TradeStatus.ACTIVE.value,
                )
                .order_by(Trade.executed_at.desc(), Trade.created_at.desc(), Trade.id.desc())
            ).all()
        )
        latest_sell_by_security: dict[str, Trade] = {}
        for trade in sell_trades:
            existing = latest_sell_by_security.get(trade.security_id)
            order_key = (_as_utc(trade.executed_at), _as_utc(trade.created_at), trade.id)
            if existing is None or order_key > (
                _as_utc(existing.executed_at),
                _as_utc(existing.created_at),
                existing.id,
            ):
                latest_sell_by_security[trade.security_id] = trade

        results: list[dict[str, Any]] = []
        for item, security in rows:
            canonical = QuoteService.security_symbol(security)
            snapshot = snapshots_by_symbol.get(canonical)
            quote_price = snapshot.last_price if snapshot is not None else None
            last_sell = latest_sell_by_security.get(security.id)
            last_sell_price = last_sell.price if last_sell is not None else None
            vs_last_sell_pct = AccountingService.price_diff_pct(quote_price, last_sell_price)
            vs_target_buy_pct = AccountingService.price_diff_pct(quote_price, item.target_buy_price)
            hit_buy_target = bool(
                quote_price is not None
                and item.target_buy_price is not None
                and quote_price <= item.target_buy_price
            )
            realized = round_amount(realized_by_security.get(security.id, Decimal("0")))
            amp = amplitudes_by_security.get(security.id)

            results.append(
                {
                    "id": item.id,
                    "accountId": item.account_id,
                    "securityId": security.id,
                    "strategyId": strategy_by_security.get(security.id, default_strategy_id),
                    "market": security.market,
                    "symbol": security.symbol,
                    "name": security.name,
                    "securityType": security.security_type,
                    "currency": currency_for_market(security.market),
                    "sortOrder": item.sort_order,
                    "targetBuyPrice": _fmt_decimal(item.target_buy_price, 6),
                    "alertEnabled": bool(item.alert_enabled),
                    "note": item.note,
                    "hasOpenPosition": security.id in open_security_ids,
                    "realizedPnl": _fmt_decimal(realized, 2),
                    "lastSellPrice": _fmt_decimal(last_sell_price, 6),
                    "lastSellExecutedAt": (
                        format_beijing_iso(last_sell.executed_at)
                        if last_sell is not None
                        else None
                    ),
                    "vsLastSellPct": _fmt_decimal(vs_last_sell_pct, 6),
                    "vsTargetBuyPct": _fmt_decimal(vs_target_buy_pct, 6),
                    "hitBuyTarget": hit_buy_target,
                    "avgAmplitude20d": (
                        _fmt_decimal(amp.avg_amplitude_20d, 6) if amp is not None else None
                    ),
                    "avgAmplitudeDays": amp.sample_days if amp is not None else None,
                    "avgAmplitudeAsOf": amp.as_of_date if amp is not None else None,
                    "quote": _quote_dto(security, snapshot),
                    "security": _security_dto(security),
                    "createdAt": format_beijing_iso(item.created_at),
                    "updatedAt": format_beijing_iso(item.updated_at),
                }
            )
        return results
