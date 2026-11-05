# M0 账本 REST API (m0-ledger-api) — Technical Design

## 1. API Module Layout

```text
backend/app/
├── main.py                      # FastAPI app, middleware, exception handlers, router mount
├── api/
│   ├── deps.py                  # DB Session & UoW dependency injection
│   ├── errors.py                # DomainError -> standard JSON {"error": {"code", "message", "details"}}
│   ├── schemas/
│   │   ├── common.py            # DecimalStr, ErrorResponse
│   │   ├── security.py          # SecurityRead, WatchlistItemCreate/Read
│   │   ├── trade.py             # BuyTradeRequest, SellPreviewRequest, SellTradeRequest, VoidTradeRequest, TradeRead
│   │   ├── position.py          # PositionRead, LotRead, TradeMatchRead, CycleRead
│   │   ├── target.py            # ProfitTargetCreate, ProfitTargetPatch, ProfitTargetRead
│   │   └── analytics.py         # AnalyticsSummaryRead
│   └── v1/
│       ├── router.py
│       ├── securities.py
│       ├── watchlist.py
│       ├── trades.py
│       ├── positions.py
│       ├── targets.py
│       └── analytics.py
```

## 2. Error Contract & Concurrency Control

- **Standard Error Mapping**:
  - `InsufficientLotQtyError` -> HTTP `409`, `code="INSUFFICIENT_LOT_QTY"`
  - `LotHasActiveMatchesError` -> HTTP `409`, `code="LOT_HAS_ACTIVE_MATCHES"`
  - `InvalidMatchChronologyError` -> HTTP `422`, `code="INVALID_MATCH_CHRONOLOGY"`
  - `TargetQtyExceedsRemainingError` -> HTTP `422`, `code="TARGET_QTY_EXCEEDS_REMAINING"`
  - `EntityNotFoundError` -> HTTP `404`, `code="NOT_FOUND"`
  - `RequestValidationError` -> HTTP `422`, `code="VALIDATION_ERROR"`
- **SQLite Write Serialization**:
  - 写接口（`POST /trades/buy`, `POST /trades/sell`, `POST /trades/{id}/void`, `POST/PATCH/DELETE /targets`）在进入业务逻辑前执行 `BEGIN IMMEDIATE`，保证 SQLite 单写者串行化，彻底消除并发超卖竞态。
