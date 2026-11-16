from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import Settings
from app.market_data import EastmoneyQuoteProvider, MockQuoteProvider, create_quote_provider


def test_eastmoney_parses_exact_quotes_market_mapping_and_times() -> None:
    calls: list[tuple[str, float]] = []
    fixed_now = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)

    def transport(request, timeout):
        calls.append((request.full_url, timeout))
        return json.dumps(
            {
                "data": {
                    "diff": [
                        {
                            "f12": 600519,
                            "f13": 1,
                            "f2": 123.45,
                            "f3": 0,
                            "f18": 120.10,
                            "f124": 1763260200,
                        },
                        {
                            "f12": "002594",
                            "f13": 0,
                            "f2": "88.100",
                            "f3": "-1.25",
                            "f18": "89.200",
                            "f124": 1763260200,
                        },
                        {
                            "f12": "430047",
                            "f13": 0,
                            "f2": "5.25",
                            "f3": "2.50",
                            "f18": "5.12",
                            "f124": 1763260200,
                        },
                    ]
                }
            }
        ).encode()

    provider = EastmoneyQuoteProvider(
        timeout_seconds="2.5",
        transport=transport,
        now=lambda: fixed_now,
    )
    quotes = provider.get_quotes(
        ["SH:600519", "SZ:002594", "BJ:430047", "US:AAPL", "bad"]
    )

    assert len(calls) == 1
    query = parse_qs(urlparse(calls[0][0]).query)
    assert query["secids"] == ["1.600519,0.002594,0.430047"]
    assert calls[0][1] == 2.5
    assert quotes["SH:600519"].last_price == Decimal("123.450000")
    assert quotes["SH:600519"].change_pct == Decimal("0.000000")
    assert quotes["SZ:002594"].prev_close == Decimal("89.200000")
    assert quotes["BJ:430047"].symbol == "BJ:430047"
    assert quotes["SH:600519"].source_time == datetime(
        2025, 11, 16, 2, 30, tzinfo=UTC
    )
    assert quotes["SH:600519"].fetched_at == fixed_now
    assert quotes["SH:600519"].provider == "eastmoney"


def test_eastmoney_skips_invalid_rows_and_recomputes_missing_change() -> None:
    def transport(_request, _timeout):
        return json.dumps(
            {
                "data": {
                    "diff": [
                        {"f12": "600000", "f13": 1, "f2": "-", "f18": 10, "f3": 1},
                        {"f12": "600001", "f13": 1, "f2": 0, "f18": 10, "f3": 1},
                        {"f12": "600002", "f13": 1, "f2": 11, "f18": 0, "f3": 1},
                        {"f12": "600003", "f13": 1, "f2": 11, "f18": 10, "f3": "-"},
                    ]
                }
            }
        ).encode()

    provider = EastmoneyQuoteProvider(transport=transport)
    quotes = provider.get_quotes(
        ["SH:600000", "SH:600001", "SH:600002", "SH:600003"]
    )

    assert list(quotes) == ["SH:600003"]
    assert quotes["SH:600003"].change_pct == Decimal("10.000000")


def test_eastmoney_splits_batches_and_limits_total_work() -> None:
    requested_batches: list[list[str]] = []

    def transport(request, _timeout):
        query = parse_qs(urlparse(request.full_url).query)
        secids = query["secids"][0].split(",")
        requested_batches.append(secids)
        rows = [
            {"f12": secid.split(".")[1], "f13": 1, "f2": "10", "f18": "9"}
            for secid in secids
        ]
        return json.dumps({"data": {"diff": rows}}).encode()

    provider = EastmoneyQuoteProvider(transport=transport, max_batch_size=100)
    symbols = [f"SH:{index:06d}" for index in range(101)]
    quotes = provider.get_quotes(symbols)

    assert len(requested_batches) == 2
    assert [len(batch) for batch in requested_batches] == [100, 1]
    assert len(quotes) == 101
    with pytest.raises(ValueError, match="cumulative symbol limit"):
        provider.get_quotes([f"SH:{index:06d}" for index in range(1001)])


def test_eastmoney_keeps_successful_batches_when_a_later_batch_fails() -> None:
    call_count = 0

    def transport(request, _timeout):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise TimeoutError("second batch timed out")
        query = parse_qs(urlparse(request.full_url).query)
        code = query["secids"][0].split(".", 1)[1]
        return json.dumps(
            {"data": {"diff": [{"f12": code, "f13": 1, "f2": "10", "f18": "9"}]}}
        ).encode()

    provider = EastmoneyQuoteProvider(transport=transport, max_batch_size=1)
    quotes = provider.get_quotes(["SH:600519", "SH:600520"])

    assert list(quotes) == ["SH:600519"]
    assert quotes["SH:600519"].last_price == Decimal("10.000000")


def test_eastmoney_raises_when_every_batch_fails() -> None:
    def transport(_request, _timeout):
        raise TimeoutError("quote service timed out")

    provider = EastmoneyQuoteProvider(transport=transport, max_batch_size=1)

    with pytest.raises(TimeoutError, match="timed out"):
        provider.get_quotes(["SH:600519", "SH:600520"])


def test_eastmoney_rejects_oversize_response_and_health_stays_offline() -> None:
    provider = EastmoneyQuoteProvider(transport=lambda _request, _timeout: b"x" * 10)
    provider._max_response_bytes = 5

    with pytest.raises(ValueError, match="size limit"):
        provider.get_quotes(["SH:600519"])
    assert provider.health().healthy is True


def test_settings_and_factory_validate_provider_configuration(monkeypatch) -> None:
    monkeypatch.setenv("QUOTE_PROVIDER", "mock")
    monkeypatch.setenv("QUOTE_TIMEOUT_SECONDS", "3.5")
    settings = Settings.from_env()

    assert settings.quote_provider == "mock"
    assert settings.quote_timeout_seconds == Decimal("3.5")
    assert isinstance(create_quote_provider(settings), MockQuoteProvider)

    monkeypatch.setenv("QUOTE_PROVIDER", "unknown")
    with pytest.raises(ValueError, match="QUOTE_PROVIDER"):
        Settings.from_env()

    monkeypatch.setenv("QUOTE_PROVIDER", "eastmoney")
    monkeypatch.setenv("QUOTE_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError, match="QUOTE_TIMEOUT_SECONDS"):
        Settings.from_env()
