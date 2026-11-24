from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.domain.exceptions import EntityNotFoundError
from app.domain.markets import currency_for_market
from app.domain.precision import round_price
from app.market_data.provider import QuoteSnapshot
from app.market_data.security_info import (
    IntradayPoint,
    IntradayService,
    IntradaySnapshot,
    SecurityInfoProvider,
)
from app.market_data.service import QuoteService
from app.models.auth import User
from app.models.ledger import Security

router = APIRouter()
security_info_provider = SecurityInfoProvider()
intraday_service = IntradayService(security_info_provider)


def _format_quote_payload(
    security: Security,
    quote: QuoteSnapshot | None,
    *,
    snapshot_stale: bool,
) -> dict[str, Any] | None:
    """将分时接口返回的最新报价（含盘中估算元数据）序列化为前端字典。"""
    if quote is None:
        return None
    currency = currency_for_market(security.market)
    return {
        "symbol": quote.symbol,
        "securityId": security.id,
        "market": security.market,
        "currency": currency,
        "lastPrice": format(quote.last_price, ".6f"),
        "prevClose": format(quote.prev_close, ".6f") if quote.prev_close is not None else None,
        "changePct": format(quote.change_pct, ".6f") if quote.change_pct is not None else None,
        "sourceTime": quote.source_time.isoformat() if quote.source_time else None,
        "fetchedAt": quote.fetched_at.isoformat(),
        "provider": quote.provider,
        "isStale": quote.is_stale or snapshot_stale,
        "marketSession": quote.market_session,
        "isEstimated": quote.is_estimated,
        "officialNav": format(quote.official_nav, ".6f") if quote.official_nav is not None else None,
        "officialNavDate": quote.official_nav_date,
        "proxySymbol": quote.proxy_symbol,
        "proxyName": quote.proxy_name,
        "proxyChangePct": (
            format(quote.proxy_change_pct, ".6f") if quote.proxy_change_pct is not None else None
        ),
        "trackingRatio": (
            format(quote.tracking_ratio, ".6f") if quote.tracking_ratio is not None else None
        ),
    }


def _build_estimated_intraday_points(
    *,
    official_nav: Decimal,
    proxy_points: tuple[IntradayPoint, ...],
    proxy_prev_close: Decimal | None,
    tracking_ratio: Decimal,
) -> tuple[IntradayPoint, ...]:
    """将参考标的当日分钟价格序列按仓位系数换算为场外基金估算净值序列。"""
    if proxy_prev_close is None or proxy_prev_close <= 0 or not proxy_points:
        return ()
    converted: list[IntradayPoint] = []
    for point in proxy_points:
        point_change_pct = (point.price - proxy_prev_close) / proxy_prev_close * Decimal("100")
        estimated_nav = round_price(
            official_nav * (Decimal("1") + point_change_pct * tracking_ratio / Decimal("100"))
        )
        if estimated_nav > 0:
            converted.append(IntradayPoint(time=point.time, price=estimated_nav))
    return tuple(converted)


@router.get("/securities/lookup")
async def lookup_securities(
    query: str = Query(min_length=1, max_length=80),
    _current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """每次显式查找均访问外部接口，失败时保留本地候选和错误说明。"""
    existing = list(session.scalars(select(Security)))
    by_symbol = {(row.market.upper(), row.symbol.upper()): row for row in existing}
    keyword = query.strip().upper()
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for row in existing:
        if keyword in f"{row.market}:{row.symbol} {row.name or ''}".upper():
            candidates[(row.market.upper(), row.symbol.upper())] = {
                "id": row.id,
                "market": row.market,
                "symbol": row.symbol,
                "name": row.name,
                "securityType": row.security_type,
                "currency": currency_for_market(row.market),
            }
    error = None
    try:
        external = await asyncio.to_thread(security_info_provider.search, query)
        for candidate in external:
            local = by_symbol.get((candidate.market, candidate.symbol))
            candidates[(candidate.market, candidate.symbol)] = {
                "id": local.id if local else None,
                "market": candidate.market,
                "symbol": candidate.symbol,
                "name": candidate.name,
                "securityType": local.security_type if local else candidate.security_type,
                "currency": currency_for_market(candidate.market),
            }
    except Exception:
        error = "外部证券名称查询失败，已保留本地结果；可稍后重试"
    return {"securities": list(candidates.values()), "error": error}


@router.get("/securities/{security_id}/intraday")
async def read_intraday(
    security_id: str,
    series: Literal["auto", "nav"] = Query(default="auto"),
    _current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回真实日内曲线、场外基金估算分时或近期净值曲线，并将原始真实行情写入共享缓存。"""
    security = session.get(Security, security_id)
    if security is None:
        raise EntityNotFoundError("找不到证券", details={"security_id": security_id})
    quote_service = QuoteService()
    canonical = f"{security.market}:{security.symbol}"

    # 已绑定参考标的的场外基金：series=auto 时优先基于参考标的分钟序列换算当日估算分时，
    # 官方净值已更新至当日或参考分时不可用时自动平滑回退近期官方净值曲线。
    if security.market.upper() == "OF" and security.proxy_symbol and series == "auto":
        fund_nav_snapshot = await intraday_service.read(canonical)
        proxy_snapshot = await intraday_service.read(security.proxy_symbol)
        proxy_security = quote_service.proxy_securities_for(session, [security]).get(
            security.proxy_symbol.upper()
        )
        try:
            if fund_nav_snapshot.quote is not None and not fund_nav_snapshot.quote.is_stale:
                quote_service.upsert(session, [fund_nav_snapshot.quote])
            if (
                proxy_security is not None
                and proxy_snapshot.quote is not None
                and not proxy_snapshot.quote.is_stale
            ):
                quote_service.upsert(session, [proxy_snapshot.quote])
            session.commit()
        except Exception:
            session.rollback()
            raise

        official_quote = fund_nav_snapshot.quote
        if official_quote is None:
            cached_official = quote_service.read_cached(
                session, [security], apply_proxy_estimate=False
            )
            official_quote = cached_official[0] if cached_official else None

        proxy_quote = proxy_snapshot.quote
        if proxy_quote is None and proxy_security is not None:
            cached_proxy = quote_service.read_cached(
                session, [proxy_security], apply_proxy_estimate=False
            )
            proxy_quote = cached_proxy[0] if cached_proxy else None

        effective_quote = quote_service.resolve_effective_snapshot(
            security, official_quote, proxy_quote
        )
        tracking_ratio = round_price(security.tracking_ratio or Decimal("0.950000"))
        if (
            effective_quote is not None
            and effective_quote.is_estimated
            and official_quote is not None
            and proxy_snapshot.points
            and proxy_quote is not None
            and proxy_quote.prev_close is not None
            and proxy_quote.prev_close > 0
        ):
            estimated_points = _build_estimated_intraday_points(
                official_nav=official_quote.last_price,
                proxy_points=proxy_snapshot.points,
                proxy_prev_close=proxy_quote.prev_close,
                tracking_ratio=tracking_ratio,
            )
            if estimated_points:
                snapshot = IntradaySnapshot(
                    symbol=canonical,
                    trading_date=proxy_snapshot.trading_date,
                    points=estimated_points,
                    quote=effective_quote,
                    fetched_at=proxy_snapshot.fetched_at,
                    provider=f"{fund_nav_snapshot.provider}+proxy",
                    is_stale=proxy_snapshot.is_stale,
                    error=proxy_snapshot.error,
                    series_kind="estimated_intraday",
                )
                currency = currency_for_market(security.market)
                return {
                    "symbol": snapshot.symbol,
                    "currency": currency,
                    "tradingDate": snapshot.trading_date,
                    "seriesKind": snapshot.series_kind,
                    "points": [
                        {"time": point.time.isoformat(), "price": format(point.price, ".6f")}
                        for point in snapshot.points
                    ],
                    "quote": _format_quote_payload(
                        security, snapshot.quote, snapshot_stale=snapshot.is_stale
                    ),
                    "provider": snapshot.provider,
                    "fetchedAt": snapshot.fetched_at.isoformat(),
                    "isStale": snapshot.is_stale,
                    "error": snapshot.error,
                }

        currency = currency_for_market(security.market)
        return {
            "symbol": fund_nav_snapshot.symbol,
            "currency": currency,
            "tradingDate": fund_nav_snapshot.trading_date,
            "seriesKind": fund_nav_snapshot.series_kind,
            "points": [
                {"time": point.time.isoformat(), "price": format(point.price, ".6f")}
                for point in fund_nav_snapshot.points
            ],
            "quote": _format_quote_payload(
                security,
                effective_quote or fund_nav_snapshot.quote,
                snapshot_stale=fund_nav_snapshot.is_stale,
            ),
            "provider": fund_nav_snapshot.provider,
            "fetchedAt": fund_nav_snapshot.fetched_at.isoformat(),
            "isStale": fund_nav_snapshot.is_stale,
            "error": fund_nav_snapshot.error,
        }

    snapshot = await intraday_service.read(canonical)
    quote = snapshot.quote
    try:
        if quote is not None and not quote.is_stale:
            quote_service.upsert(session, [quote])
        else:
            quote_service.mark_stale(session, [security])
        session.commit()
    except Exception:
        session.rollback()
        raise

    effective_quote = quote
    if security.market.upper() == "OF" and security.proxy_symbol:
        cached = quote_service.read_cached(session, [security])
        if cached:
            effective_quote = cached[0]

    currency = currency_for_market(security.market)
    return {
        "symbol": snapshot.symbol,
        "currency": currency,
        "tradingDate": snapshot.trading_date,
        "seriesKind": snapshot.series_kind,
        "points": [
            {"time": point.time.isoformat(), "price": format(point.price, ".6f")}
            for point in snapshot.points
        ],
        "quote": _format_quote_payload(
            security, effective_quote, snapshot_stale=snapshot.is_stale
        ),
        "provider": snapshot.provider,
        "fetchedAt": snapshot.fetched_at.isoformat(),
        "isStale": snapshot.is_stale,
        "error": snapshot.error,
    }
