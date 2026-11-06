# Market Data Guidelines

## Scenario: Shared quote cache and `/api/v1/quotes`

### 1. Scope / Trigger

This contract applies to quote providers, cache persistence, refresh orchestration,
and target reminder evaluation. Quote data is derived display data and must never
block or rewrite ledger facts.

### 2. Signatures

- `QuoteProvider.get_quotes(symbols: Sequence[str]) -> dict[str, QuoteSnapshot]`
- `QuoteProvider.health() -> ProviderHealth`
- `QuoteCollector.collect(session, securities, refresh=True, now=None)`
- `QuoteService.read_cached(session, securities, now=None)`
- `GET /api/v1/quotes?symbols=SH:600519,SZ:002594&refresh=false`
- Development startup uses `uv run` with a project-local cache and invokes
  `python -m uvicorn` so the server and dependencies use the same interpreter.
- US securities use an `America/New_York` calendar. Runtime tzdata should be
  installed for daylight-saving correctness; a fixed-offset fallback only keeps
  offline startup from crashing.

### 3. Contracts

`QuoteSnapshot` carries `symbol`, `last_price`, optional `prev_close`,
`change_pct`, `source_time`, `fetched_at`, `provider`, and `is_stale`.
Symbols are normalized to uppercase `MARKET:CODE`. Cache rows preserve the last
valid price and set `is_stale=True` when the TTL expires or refresh fails.
Refreshes are serialized by one async lock and throttled by a minimum interval;
three consecutive provider failures open an exponential backoff circuit.

Only a fresh snapshot whose price reaches an active, alert-enabled target may
set `ProfitTarget.status` to `TRIGGERED`. No quote operation creates trades.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Unknown requested symbol | Return an item with null quote fields and `isStale=true` |
| Cache younger than TTL | Return cached snapshot without provider refresh |
| Cache older than TTL | Return last price with `isStale=true` |
| Provider timeout/error | Preserve last cache value and mark it stale |
| Three consecutive failures | Open circuit and serve stale cache during backoff |
| Fresh price reaches target | Set target to `TRIGGERED` with `triggered_at` |
| Stale price reaches target | Do not trigger the target |

### 5. Good / Base / Bad Cases

- Good: two concurrent refresh requests share one provider call and both receive
  the same cache result.
- Base: an offline provider leaves ledger buy, sell, void, and query routes usable.
- Bad: replacing a failed quote with zero, or using a stale price to trigger an
  alert.
- Windows/offline: if IANA tzdata is unavailable, Asia/Shanghai may safely fall
  back to fixed UTC+8 because this market timezone has no DST transition.

### 6. Tests Required

- Calendar tests cover trading sessions, holidays, and TTL expiry.
- Provider tests cover symbol normalization, change calculation, and fallback.
- Integration tests assert cache persistence, stale fallback, circuit backoff,
  single-flight refresh, and fresh-versus-stale target triggering.
- API tests assert the quote response fields and unknown-symbol behavior.

### 7. Wrong vs Correct

#### Wrong

```python
row.last_price = Decimal("0")
row.is_stale = False
```

#### Correct

```python
row.last_price = previous.last_price
row.is_stale = True
```

A provider failure must preserve the previous valid value and make its age
visible to the caller.
