"""长桥行情适配层单元测试：完全使用注入 mock 上下文，不访问公网。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.config import Settings
from app.market_data.calendar import AShareTradingCalendar, USTradingCalendar
from app.market_data.factory import create_quote_provider
from app.market_data.longbridge_adapter import (
    LongbridgeBaseline,
    LongbridgeCredentials,
    LongbridgeQuoteProvider,
    LongbridgeQuoteStream,
    PriorityFallbackQuoteProvider,
    from_longbridge_symbol,
    merge_push_quote,
    parse_security_quote,
    to_longbridge_symbol,
)
from app.market_data.provider import MockQuoteProvider, QuoteSnapshot


@dataclass
class _FakePrePostQuote:
    """模拟长桥盘前/盘后/夜盘扩展报价。"""

    last_done: Decimal
    timestamp: datetime


@dataclass
class _FakeSecurityQuote:
    """模拟长桥 SecurityQuote 基线快照对象。"""

    symbol: str
    last_done: Decimal
    prev_close: Decimal | None = None
    timestamp: datetime | None = None
    pre_market_quote: _FakePrePostQuote | None = None
    post_market_quote: _FakePrePostQuote | None = None
    overnight_quote: _FakePrePostQuote | None = None


@dataclass
class _FakePushQuote:
    """模拟长桥 PushQuote 增量推送事件对象。"""

    last_done: Decimal
    timestamp: datetime | None = None
    trade_session: Any = "Normal"


class _FakeSubType:
    """模拟长桥 SubType 枚举。"""

    Quote = "Quote"


class _FakeQuoteContext:
    """模拟长桥 QuoteContext，支持记录调用与手动触发推送回调。"""

    def __init__(self, quotes_by_lb: dict[str, _FakeSecurityQuote]) -> None:
        """初始化模拟行情基线与调用记录容器。"""
        self._quotes_by_lb = quotes_by_lb
        self.on_quote_cb: Any = None
        self.subscribed: list[tuple[list[str], list[Any], bool]] = []
        self.unsubscribed: list[tuple[list[str], list[Any]]] = []
        self.quote_calls: list[list[str]] = []
        self.closed = False

    def set_on_quote(self, callback: Any) -> None:
        """注册行情推送回调。"""
        self.on_quote_cb = callback

    def quote(self, symbols: list[str]) -> list[_FakeSecurityQuote]:
        """返回请求标的对应的模拟基线快照。"""
        self.quote_calls.append(list(symbols))
        return [self._quotes_by_lb[s] for s in symbols if s in self._quotes_by_lb]

    def subscribe(
        self,
        symbols: list[str],
        sub_types: list[Any],
        *,
        is_first_push: bool = True,
    ) -> None:
        """记录订阅请求。"""
        self.subscribed.append((list(symbols), list(sub_types), is_first_push))

    def unsubscribe(self, symbols: list[str], sub_types: list[Any]) -> None:
        """记录退订请求。"""
        self.unsubscribed.append((list(symbols), list(sub_types)))

    def close(self) -> None:
        """标记连接已关闭。"""
        self.closed = True


def test_longbridge_symbol_mapping_roundtrip_and_exclusions() -> None:
    """沪深/美股（含点号代码）应支持双向映射，北交所与场外基金返回 None。"""
    assert to_longbridge_symbol("SH:600519") == "600519.SH"
    assert to_longbridge_symbol("SZ:000001") == "000001.SZ"
    assert to_longbridge_symbol("US:AAPL") == "AAPL.US"
    assert to_longbridge_symbol("US:BRK.B") == "BRK.B.US"
    assert to_longbridge_symbol("BJ:430047") is None
    assert to_longbridge_symbol("OF:000001") is None

    assert from_longbridge_symbol("600519.SH") == "SH:600519"
    assert from_longbridge_symbol("000001.SZ") == "SZ:000001"
    assert from_longbridge_symbol("AAPL.US") == "US:AAPL"
    assert from_longbridge_symbol("BRK.B.US") == "US:BRK.B"
    assert from_longbridge_symbol("0700.HK") is None


def test_parse_security_quote_and_merge_push_quote_with_us_sessions() -> None:
    """应基于 SecurityQuote 基线精确计算增量推送涨跌幅，并正确处理美股盘后基准与非正数过滤。"""
    a_cal = AShareTradingCalendar()
    us_cal = USTradingCalendar()
    # 2026-11-25 14:35 UTC = 09:35 EST (美股常规盘)
    regular_time = datetime(2026, 11, 25, 14, 35, tzinfo=UTC)
    parsed = parse_security_quote(
        _FakeSecurityQuote(
            symbol="AAPL.US",
            last_done=Decimal("200.00"),
            prev_close=Decimal("190.00"),
            timestamp=regular_time,
        ),
        fetched_at=regular_time,
        a_calendar=a_cal,
        us_calendar=us_cal,
    )
    assert parsed is not None
    symbol, snapshot, baseline = parsed
    assert symbol == "US:AAPL"
    assert snapshot.last_price == Decimal("200.000000")
    assert snapshot.prev_close == Decimal("190.000000")
    assert snapshot.market_session == "regular"
    assert baseline.regular_close == Decimal("200.00")

    # 盘后推送：基准价应为常规收盘价 200.00，上涨到 204.00 => +2.000000%
    post_time = datetime(2026, 11, 25, 21, 30, tzinfo=UTC)
    merged = merge_push_quote(
        "AAPL.US",
        _FakePushQuote(last_done=Decimal("204.00"), timestamp=post_time, trade_session="Post"),
        baseline=baseline,
        fetched_at=post_time,
        a_calendar=a_cal,
        us_calendar=us_cal,
    )
    assert merged is not None
    _, post_snap, _ = merged
    assert post_snap.last_price == Decimal("204.000000")
    assert post_snap.prev_close == Decimal("200.000000")
    assert post_snap.change_pct == Decimal("2.000000")
    assert post_snap.market_session == "post"

    # 非正数价格应直接忽略
    ignored = merge_push_quote(
        "AAPL.US",
        _FakePushQuote(last_done=Decimal("0"), timestamp=post_time, trade_session="Post"),
        baseline=LongbridgeBaseline(prev_close=Decimal("190.00")),
        fetched_at=post_time,
        a_calendar=a_cal,
        us_calendar=us_cal,
    )
    assert ignored is None


def test_priority_fallback_provider_uses_longbridge_first_and_fallbacks_bj_of() -> None:
    """长桥主源负责 SH/US，北交所与场外基金及缺失标的应由备源补齐。"""
    now = datetime(2026, 11, 25, 2, 0, tzinfo=UTC)
    fake_ctx = _FakeQuoteContext(
        {
            "600519.SH": _FakeSecurityQuote(
                symbol="600519.SH",
                last_done=Decimal("1520.50"),
                prev_close=Decimal("1500.00"),
                timestamp=now,
            ),
        }
    )
    creds = LongbridgeCredentials(app_key="k", app_secret="s", access_token="t")
    lb_provider = LongbridgeQuoteProvider(
        creds,
        context_factory=lambda _: (fake_ctx, _FakeSubType),
        now=lambda: now,
    )
    fallback_provider = MockQuoteProvider(
        {
            "SH:600519": QuoteSnapshot(
                symbol="SH:600519", last_price=Decimal("1400.00"), provider="eastmoney"
            ),
            "BJ:430047": QuoteSnapshot(
                symbol="BJ:430047", last_price=Decimal("12.34"), provider="eastmoney"
            ),
            "OF:000001": QuoteSnapshot(
                symbol="OF:000001", last_price=Decimal("1.5000"), provider="eastmoney-fund"
            ),
        }
    )
    composite = PriorityFallbackQuoteProvider(lb_provider, fallback_provider, name="longbridge")

    result = composite.get_quotes(["SH:600519", "BJ:430047", "OF:000001"])
    assert result["SH:600519"].provider == "longbridge"
    assert result["SH:600519"].last_price == Decimal("1520.500000")
    assert result["BJ:430047"].provider == "eastmoney"
    assert result["OF:000001"].provider == "eastmoney-fund"


def test_longbridge_stream_subscription_cap_and_threadsafe_push() -> None:
    """推流管理器应遵守订阅上限优先级裁剪，并将后台推送安全投递至 asyncio 事件循环。"""
    now = datetime(2026, 11, 25, 2, 0, tzinfo=UTC)
    fake_ctx = _FakeQuoteContext(
        {
            "600519.SH": _FakeSecurityQuote(
                symbol="600519.SH",
                last_done=Decimal("1500.00"),
                prev_close=Decimal("1480.00"),
                timestamp=now,
            ),
            "000001.SZ": _FakeSecurityQuote(
                symbol="000001.SZ",
                last_done=Decimal("11.00"),
                prev_close=Decimal("10.00"),
                timestamp=now,
            ),
        }
    )
    creds = LongbridgeCredentials(app_key="k", app_secret="s", access_token="t")
    stream = LongbridgeQuoteStream(
        creds,
        context_factory=lambda _: (fake_ctx, _FakeSubType),
        now=lambda: now,
        max_subscriptions=2,
        reconnect_delays=(0.05,),
    )
    selected = stream.select_subscriptions(
        ["SH:600519", "SZ:000001", "US:AAPL", "OF:000001"],
        priority_order=["SH:600519", "SZ:000001", "US:AAPL"],
    )
    assert selected == ["600519.SH", "000001.SZ"]

    async def _exercise() -> list[dict[str, QuoteSnapshot]]:
        received: list[dict[str, QuoteSnapshot]] = []
        done = asyncio.Event()

        def _on_snapshots(batch: dict[str, QuoteSnapshot]) -> None:
            received.append(batch)
            if any(
                snap.last_price == Decimal("1510.000000")
                for item in received
                for snap in item.values()
            ):
                done.set()

        stream.sync_subscriptions(["SH:600519", "SZ:000001"])
        stream.start(asyncio.get_running_loop(), _on_snapshots)
        try:
            for _ in range(50):
                if fake_ctx.on_quote_cb is not None and fake_ctx.subscribed:
                    break
                await asyncio.sleep(0.02)
            assert fake_ctx.on_quote_cb is not None
            fake_ctx.on_quote_cb(
                "600519.SH",
                _FakePushQuote(last_done=Decimal("1510.00"), timestamp=now, trade_session="Normal"),
            )
            await asyncio.wait_for(done.wait(), timeout=2.0)
        finally:
            stream.stop()
        return received

    batches = asyncio.run(_exercise())
    pushed = [
        snap
        for batch in batches
        for sym, snap in batch.items()
        if sym == "SH:600519" and snap.last_price == Decimal("1510.000000")
    ]
    assert len(pushed) == 1
    assert pushed[0].prev_close == Decimal("1480.000000")
    assert fake_ctx.closed is True


def test_factory_creates_longbridge_provider_without_network() -> None:
    """create_quote_provider 在 longbridge 配置下应无网络开销返回优先级组合源。"""
    cfg = Settings(
        quote_provider="longbridge",
        longbridge_app_key="k",
        longbridge_app_secret="s",
        longbridge_access_token="t",
    )
    provider = create_quote_provider(cfg)
    assert isinstance(provider, PriorityFallbackQuoteProvider)
    assert provider.name == "longbridge"
    assert provider.health().healthy is True


def test_longbridge_provider_get_daily_candlesticks_and_error_recovery() -> None:
    """长桥日 K 拉取应正确映射代码、跳过不支持市场，并在异常后重置上下文。"""
    import pytest

    @dataclass
    class _FakeCandlestick:
        """模拟长桥日 K 线对象。"""

        timestamp: datetime
        open: Decimal
        high: Decimal
        low: Decimal
        close: Decimal

    class _CandleCtx(_FakeQuoteContext):
        """扩展支持 candlesticks 调用记录与故障注入的模拟上下文。"""

        def __init__(self, *, fail_once: bool = False) -> None:
            """初始化日 K 调用记录与故障标志。"""
            super().__init__({})
            self.fail_once = fail_once
            self.candle_calls: list[tuple[Any, ...]] = []

        def candlesticks(self, *args: Any) -> list[_FakeCandlestick]:
            """记录参数并返回模拟日 K，或按需抛出异常。"""
            self.candle_calls.append(args)
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("socket closed")
            return [
                _FakeCandlestick(
                    timestamp=datetime(2026, 11, 24, 5, 0, tzinfo=UTC),
                    open=Decimal("100"),
                    high=Decimal("105"),
                    low=Decimal("99"),
                    close=Decimal("102"),
                )
            ]

    created_ctxs: list[_CandleCtx] = []

    def _factory(_creds: LongbridgeCredentials) -> tuple[_CandleCtx, type[_FakeSubType]]:
        """每次建连创建新的模拟上下文（首次模拟断连，二次成功）。"""
        ctx = _CandleCtx(fail_once=len(created_ctxs) == 0)
        created_ctxs.append(ctx)
        return ctx, _FakeSubType

    creds = LongbridgeCredentials(app_key="k", app_secret="s", access_token="t")
    provider = LongbridgeQuoteProvider(creds, context_factory=_factory)

    # 不支持的市场直接返回空，不建连
    assert provider.get_daily_candlesticks("BJ:430047", count=25) == []
    assert provider.get_daily_candlesticks("OF:000300", count=25) == []
    assert len(created_ctxs) == 0

    # 首次调用失败应抛出异常并清空 _ctx
    with pytest.raises(RuntimeError, match="socket closed"):
        provider.get_daily_candlesticks("US:AAPL", count=25)
    assert len(created_ctxs) == 1
    assert created_ctxs[0].candle_calls[0][0] == "AAPL.US"
    assert created_ctxs[0].candle_calls[0][2] == 25

    # 第二次调用应自动重建上下文并成功返回日 K
    candles = provider.get_daily_candlesticks("US:AAPL", count=25)
    assert len(created_ctxs) == 2
    assert len(candles) == 1
    assert candles[0].close == Decimal("102")


def test_longbridge_naive_local_timestamp_converts_to_utc_and_keeps_us_regular_session() -> None:
    """长桥 SDK 返回的本地无时区 datetime 应按系统本地时区转 UTC，避免美股盘中被误判为盘后。"""
    # 2026-11-25 14:35 UTC = 美东 09:35 EST（常规盘中），在 UTC+8 下本地 naive 时间为 22:35
    actual_utc = datetime(2026, 11, 25, 14, 35, 29, tzinfo=UTC)
    naive_local = actual_utc.astimezone().replace(tzinfo=None)

    parsed = parse_security_quote(
        _FakeSecurityQuote(
            symbol="AAPL.US",
            last_done=Decimal("350.57"),
            prev_close=Decimal("354.07"),
            timestamp=naive_local,
        ),
        fetched_at=actual_utc,
        a_calendar=AShareTradingCalendar(),
        us_calendar=USTradingCalendar(),
    )
    assert parsed is not None
    canonical, snapshot, _ = parsed
    assert canonical == "US:AAPL"
    assert snapshot.source_time == actual_utc
    assert snapshot.market_session == "regular"
