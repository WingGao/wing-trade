from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Protocol, Sequence

from app.domain.precision import ensure_decimal, round_price


def _now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_symbol(value: str) -> str:
    """Normalize provider symbols to the public ``MARKET:CODE`` form."""
    value = value.strip().upper()
    if ":" in value:
        market, symbol = value.split(":", 1)
        return f"{market}:{symbol}"
    return value


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    symbol: str
    last_price: Decimal
    prev_close: Decimal | None = None
    change_pct: Decimal | None = None
    source_time: datetime | None = None
    fetched_at: datetime = field(default_factory=_now)
    provider: str = "unknown"
    is_stale: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))
        object.__setattr__(self, "last_price", round_price(ensure_decimal(self.last_price, field="last_price")))
        if self.prev_close is not None:
            object.__setattr__(self, "prev_close", round_price(ensure_decimal(self.prev_close, field="prev_close")))
        if self.change_pct is None and self.prev_close not in (None, Decimal("0")):
            change = (self.last_price - self.prev_close) / self.prev_close * Decimal("100")
            object.__setattr__(self, "change_pct", round_price(change))
        elif self.change_pct is not None:
            object.__setattr__(self, "change_pct", round_price(ensure_decimal(self.change_pct, field="change_pct")))

    def stale(self, *, at: datetime | None = None) -> "QuoteSnapshot":
        return QuoteSnapshot(
            symbol=self.symbol,
            last_price=self.last_price,
            prev_close=self.prev_close,
            change_pct=self.change_pct,
            source_time=self.source_time,
            fetched_at=self.fetched_at,
            provider=self.provider,
            is_stale=True,
        )


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    healthy: bool
    message: str | None = None
    checked_at: datetime = field(default_factory=_now)


class QuoteProvider(Protocol):
    name: str

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        ...

    def health(self) -> ProviderHealth:
        ...


class MockQuoteProvider:
    """Deterministic provider for tests and offline development."""

    name = "mock"

    def __init__(self, quotes: Mapping[str, QuoteSnapshot | Mapping[str, object]] | None = None) -> None:
        self.quotes: dict[str, QuoteSnapshot] = {}
        self.set_quotes(quotes or {})

    def set_quotes(self, quotes: Mapping[str, QuoteSnapshot | Mapping[str, object]]) -> None:
        for symbol, value in quotes.items():
            if isinstance(value, QuoteSnapshot):
                snapshot = value
            else:
                snapshot = QuoteSnapshot(symbol=symbol, provider=self.name, **value)
            self.quotes[canonical_symbol(symbol)] = snapshot

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        return {
            canonical_symbol(symbol): self.quotes[canonical_symbol(symbol)]
            for symbol in symbols
            if canonical_symbol(symbol) in self.quotes
        }

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True)

    async def get_quotes_async(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        return await asyncio.to_thread(self.get_quotes, symbols)


class FallbackQuoteProvider:
    """Return a fallback provider's data when the primary source fails."""

    name = "fallback"

    def __init__(self, primary: QuoteProvider, fallback: QuoteProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        try:
            return self.primary.get_quotes(symbols)
        except Exception:
            return {symbol: snapshot.stale() for symbol, snapshot in self.fallback.get_quotes(symbols).items()}

    def health(self) -> ProviderHealth:
        try:
            primary = self.primary.health()
            if primary.healthy:
                return primary
        except Exception:
            pass
        return self.fallback.health()


class AKShareQuoteProvider:
    """Optional AKShare adapter; the dependency is imported only on refresh."""

    name = "akshare"

    def __init__(self, *, akshare_module: object | None = None) -> None:
        self._module = akshare_module

    def _load(self) -> object:
        if self._module is None:
            import akshare  # type: ignore[import-not-found]

            self._module = akshare
        return self._module

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        module = self._load()
        result: dict[str, QuoteSnapshot] = {}
        fetch = getattr(module, "stock_zh_a_spot_em")
        frame = fetch()
        records = list(getattr(frame, "to_dict", lambda **_: [])(orient="records"))
        for canonical in (canonical_symbol(value) for value in symbols):
            _, code = canonical.split(":", 1) if ":" in canonical else ("", canonical)
            # AKShare's spot endpoint is a synchronous SDK call.  The collector
            # invokes this method through asyncio.to_thread so it never blocks
            # the FastAPI event loop.
            row = None
            for candidate in records:
                candidate_code = str(candidate.get("代码", candidate.get("symbol", "")))
                if candidate_code == code:
                    row = candidate
                    break
            if row is None:
                continue
            last = row.get("最新价", row.get("last_price"))
            prev = row.get("昨收", row.get("prev_close"))
            if last is None:
                continue
            result[canonical] = QuoteSnapshot(
                symbol=canonical,
                last_price=last,
                prev_close=prev,
                change_pct=row.get("涨跌幅", row.get("change_pct")),
                source_time=_now(),
                provider=self.name,
            )
        return result

    def health(self) -> ProviderHealth:
        try:
            self._load()
        except Exception as exc:
            return ProviderHealth(provider=self.name, healthy=False, message=str(exc))
        return ProviderHealth(provider=self.name, healthy=True)

