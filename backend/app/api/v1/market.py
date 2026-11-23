from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.domain.exceptions import EntityNotFoundError
from app.domain.markets import currency_for_market
from app.market_data.security_info import IntradayService, SecurityInfoProvider
from app.market_data.service import QuoteService
from app.models.auth import User
from app.models.ledger import Security

router = APIRouter()
security_info_provider = SecurityInfoProvider()
intraday_service = IntradayService(security_info_provider)


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
    _current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回真实日内曲线，并将同次行情写入共享估值缓存供批次读取。"""
    security = session.get(Security, security_id)
    if security is None:
        raise EntityNotFoundError("找不到证券", details={"security_id": security_id})
    snapshot = await intraday_service.read(f"{security.market}:{security.symbol}")
    quote_service = QuoteService()
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
        "quote": {
            "symbol": snapshot.symbol,
            "securityId": security.id,
            "market": security.market,
            "currency": currency,
            "lastPrice": format(quote.last_price, ".6f"),
            "prevClose": format(quote.prev_close, ".6f") if quote.prev_close is not None else None,
            "changePct": format(quote.change_pct, ".6f") if quote.change_pct is not None else None,
            "sourceTime": quote.source_time.isoformat() if quote.source_time else None,
            "fetchedAt": quote.fetched_at.isoformat(),
            "provider": quote.provider,
            "isStale": quote.is_stale or snapshot.is_stale,
        }
        if quote
        else None,
        "provider": snapshot.provider,
        "fetchedAt": snapshot.fetched_at.isoformat(),
        "isStale": snapshot.is_stale,
        "error": snapshot.error,
    }
