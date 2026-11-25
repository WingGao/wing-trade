from __future__ import annotations

from collections.abc import Callable
from urllib.request import Request

from app.config import Settings
from app.config import settings as application_settings

from .longbridge_adapter import (
    LongbridgeCredentials,
    LongbridgeQuoteProvider,
    PriorityFallbackQuoteProvider,
)
from .provider import (
    CompositeQuoteProvider,
    EastmoneyFundQuoteProvider,
    EastmoneyQuoteProvider,
    EastmoneyUSQuoteProvider,
    FallbackUSQuoteProvider,
    MockQuoteProvider,
    QuoteProvider,
    TencentUSQuoteProvider,
)


def _create_eastmoney_composite(
    configured: Settings,
    *,
    transport: Callable[[Request, float], bytes] | None = None,
) -> CompositeQuoteProvider:
    """构建东方财富/腾讯多市场组合行情源，供默认模式及长桥备源复用。"""
    return CompositeQuoteProvider(
        [
            EastmoneyQuoteProvider(
                timeout_seconds=configured.quote_timeout_seconds,
                transport=transport,
            ),
            FallbackUSQuoteProvider(
                TencentUSQuoteProvider(
                    timeout_seconds=configured.quote_timeout_seconds,
                    transport=transport,
                ),
                EastmoneyUSQuoteProvider(
                    timeout_seconds=configured.quote_timeout_seconds,
                    transport=transport,
                ),
            ),
            EastmoneyFundQuoteProvider(
                timeout_seconds=configured.quote_timeout_seconds,
                transport=transport,
            ),
        ],
        name="eastmoney",
    )


def create_quote_provider(
    configuration: Settings | None = None,
    *,
    transport: Callable[[Request, float], bytes] | None = None,
) -> QuoteProvider:
    """按已校验配置创建行情源，导入和创建过程均不访问网络。"""
    configured = configuration or application_settings
    if configured.quote_provider == "mock":
        return MockQuoteProvider()
    if configured.quote_provider == "eastmoney":
        return _create_eastmoney_composite(configured, transport=transport)
    if configured.quote_provider == "longbridge":
        credentials = LongbridgeCredentials(
            app_key=configured.longbridge_app_key,
            app_secret=configured.longbridge_app_secret,
            access_token=configured.longbridge_access_token,
            enable_overnight=configured.longbridge_enable_overnight,
            http_url=configured.longbridge_http_url,
            quote_ws_url=configured.longbridge_quote_ws_url,
        )
        return PriorityFallbackQuoteProvider(
            primary=LongbridgeQuoteProvider(credentials),
            fallback=_create_eastmoney_composite(configured, transport=transport),
            name="longbridge",
        )
    raise ValueError(f"unsupported quote provider: {configured.quote_provider}")
