from __future__ import annotations

from typing import Final

MARKET_CURRENCY: Final[dict[str, str]] = {
    "SH": "CNY",
    "SZ": "CNY",
    "BJ": "CNY",
    "US": "USD",
}


def currency_for_market(market: str) -> str:
    try:
        return MARKET_CURRENCY[market.upper()]
    except KeyError as exc:
        raise ValueError(f"unsupported market: {market}") from exc
