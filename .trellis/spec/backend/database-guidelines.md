# Database Guidelines

## Scenario: M0 Ledger Domain and Database

### 1. Scope / Trigger

This contract applies to the SQLite ledger schema, SQLAlchemy ORM models, Alembic
migrations, and services that mutate `Trade`, `Lot`, `TradeMatch`, or `Cycle`.
The accounting boundary must preserve Decimal values exactly and every fact/state
change must remain auditable.

### 2. Signatures

- `ExactDecimal(scale: int)` stores fixed-scale text and returns `Decimal`.
- `TradeLedgerService.buy(..., qty, price, fees, client_request_id, commit=True) -> Trade`.
- `TradeLedgerService.sell(..., qty, price, matches=None, fees, client_request_id, commit=True) -> Trade`.
- `TradeLedgerService.void_trade(trade_id, reason=None, commit=True) -> Trade`.
- `MatchingService.allocate(lots_by_id, requests, sell_qty, sell_cash_in, ...) -> list[MatchAllocation]`.

### 3. Contracts

- Tables use lower snake case. IDs are UUID strings. Decimal columns use
  `ExactDecimal(4)` for quantity/amount and `ExactDecimal(6)` for price/rates.
- `trades.client_request_id` is unique per account for idempotent submissions.
- Buy cash out is `round_4(price * qty) + fees`; sell cash in is
  `round_4(price * qty) - fees`.
- Automatic matching is deterministic by `(executed_at, created_at, id)`: FIFO
  uses ascending order, LIFO uses descending order, and an omitted strategy
  remains FIFO. Automatic candidates must be OPEN, have positive remaining
  quantity, match the sell scope, and be bought no later than the sell.
- `EQUAL` 沿用最近优先排序，在能覆盖卖出的最小集合内按容量均分，整数余股按最近优先；旧小数持仓保持四位精度与数量守恒。界面默认显式传 EQUAL，未指定策略的旧 API 仍默认 FIFO。
- Manual matches must total the sell quantity and belong to the same
  account/security/strategy.
- A final match consumes the lot's current remaining cost; partial matches use
  `round_4(original_cost * matched_qty / original_qty)`. Sell-net tail rounding
  goes to the final match.
- SQLite connections enable `foreign_keys=ON` and `journal_mode=WAL`.
- State changes emit `audit_events` for `TRADE`, `LOT`, and `CYCLE` entities in the
  same session transaction.
- `DATABASE_URL` and `ECHO_SQL` are optional environment keys; defaults are
  `sqlite:///./ledger.db` and disabled SQL echo.

### 4. Validation & Error Matrix

| Condition | Required result |
|---|---|
| Quantity or price is non-positive | `InvalidQuantityError` / `InvalidPriceError` or `ValidationError` |
| Fee is negative, NaN, or infinite | `NegativeFeeError` / `TypeError` / `ValueError` |
| Manual match quantity differs from sell quantity | `MatchQuantityMismatchError` |
| Lot has insufficient quantity | `InsufficientLotQtyError` |
| Lot scope differs from sell scope | `LotOwnershipError` |
| Lot was bought after the sell | `FutureLotError` |
| Buy with active sell matches is voided | `LotHasActiveMatchesError` |
| Duplicate account/client request | Return the existing trade without adding rows |

### 5. Good / Base / Bad Cases

- Good: buy 1,000 at 10, then sell 1,000 at 11; the lot closes and realized
  P&L is 1,000 before fees.
- Base: sell across two lots; the final allocated sell-net absorbs all rounding
  residue and total allocated net equals sell cash in exactly.
- Bad: query SQLite text-backed `remaining_qty` with SQL `> 0`; load Decimal
  values and filter in Python instead.

### 6. Tests Required

- Alembic `upgrade head` and `downgrade base` on SQLite.
- Unit tests for precision rejection, FIFO/manual validation, tail allocation,
  target price solving, and the documented Section 6 cases.
- Hypothesis tests assert match quantity conservation, allocated sell-net
  conservation, and lot cost plus remaining cost conservation.
- Integration tests assert buy/sell/void state, idempotency, and `AuditEvent`
  rows for Trade/Lot/Cycle changes.

### 7. Wrong vs Correct

#### Wrong

```python
session.scalars(select(Lot).where(Lot.remaining_qty > 0))
```

#### Correct

```python
lots = list(session.scalars(select(Lot).where(Lot.status == "OPEN")))
lots = [lot for lot in lots if lot.remaining_qty > 0]
```

SQLite stores `ExactDecimal` as text, so numeric filtering belongs in Python.

### Common Mistakes

- SQLite `DateTime(timezone=True)` values may be returned without timezone
  information. Normalize persisted and request timestamps to UTC before
  chronology comparisons.
- A slotted dataclass exposes field descriptors on the class. Read configuration
  defaults from an instance (for example `defaults = Settings()`) rather than
  from `Settings.database_url`.

## Naming and Migration Rules

- Use singular model names and plural table names (`Trade` / `trades`).
- Add schema changes through a new Alembic revision; never edit an applied
  revision in place.
- Keep unique constraints and indexes named explicitly (`uq_*`, `ix_*`).
