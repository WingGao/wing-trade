from __future__ import annotations

from collections.abc import Callable
from urllib.request import Request

from app.config import Settings
from app.config import settings as application_settings

from .provider import (
    CompositeQuoteProvider,
    EastmoneyFundQuoteProvider,
    EastmoneyQuoteProvider,
    MockQuoteProvider,
    QuoteProvider,
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
        return CompositeQuoteProvider(
            [
                EastmoneyQuoteProvider(
                    timeout_seconds=configured.quote_timeout_seconds,
                    transport=transport,
                ),
                EastmoneyFundQuoteProvider(
                    timeout_seconds=configured.quote_timeout_seconds,
                    transport=transport,
                ),
            ],
            name="eastmoney",
        )
    raise ValueError(f"unsupported quote provider: {configured.quote_provider}")
