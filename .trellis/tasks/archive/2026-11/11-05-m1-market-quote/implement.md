# M1 行情采集与缓存 (m1-market-quote) — Implementation Plan

## 1. Prerequisite

- 必须先完成并通过 `11-05-m0-ledger-api`。

## 2. Ordered Checklist

- [ ] **Step 1: 实现 `QuoteProvider` 协议、A 股交易时段判定与 `AKShareQuoteProvider` (`backend/app/market_data/`)**
- [ ] **Step 2: 实现带单飞锁、退避熔断与止盈触发评估的 `QuoteCollector` 与 `QuoteService`**
- [ ] **Step 3: 实现 `GET /api/v1/quotes` 路由并在 `/api/v1/positions` 中集成最新价浮盈计算**
- [ ] **Step 4: 编写行情缓存、过期判定、熔断降级及止盈触发集成测试 (`backend/tests/integration/test_market_quote.py`)**

## 3. Validation Commands

```bash
cd backend
pytest tests/integration/test_market_quote.py -v
ruff check app tests
mypy app
```
