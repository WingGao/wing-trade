from __future__ import annotations

from typing import Final

MARKET_CURRENCY: Final[dict[str, str]] = {
    "SH": "CNY",
    "SZ": "CNY",
    "BJ": "CNY",
    "OF": "CNY",
    "US": "USD",
}


def is_open_fund_market(market: str | None) -> bool:
    """判断市场标识是否为场外开放式基金。"""
    return bool(market and market.strip().upper() == "OF")


def currency_for_market(market: str) -> str:
    """返回市场对应的结算币种，未知市场抛出 ValueError。"""
    try:
        return MARKET_CURRENCY[market.upper()]
    except KeyError as exc:
        raise ValueError(f"unsupported market: {market}") from exc
