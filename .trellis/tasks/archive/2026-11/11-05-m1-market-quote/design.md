# M1 行情采集与缓存 (m1-market-quote) — Technical Design

## 1. Module Structure

```text
backend/app/market_data/
├── __init__.py
├── provider.py          # QuoteSnapshot, QuoteProvider protocol
├── akshare_provider.py  # AKShare adapter running in asyncio.to_thread
├── calendar.py          # A-share trading session & staleness rules (Asia/Shanghai)
├── collector.py         # Background scheduler, circuit breaker, single-flight lock, target evaluator
└── service.py           # QuoteService reading/updating quote_cache
```

## 2. Circuit Breaker & Single-Flight Caching

- **Single-Flight Lock**: 使用 `asyncio.Lock` 保护上游采集，多个请求同时触发刷新时合并为单次采集。
- **Circuit Breaker**: 连续 3 次采集失败进入 `OPEN` 熔断状态（冷却 30s -> 60s -> 120s 指数退避），熔断期间直接返回 `quote_cache` 中的缓存数据并计算 `is_stale=True`。
