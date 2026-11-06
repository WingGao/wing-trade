# 美股支持 — Technical Design

## 1. Boundaries

- Keep one ledger model for A-share and US instruments. `Security.market` is
  the market discriminator (`SH`, `SZ`, or `US`), while `Account.currency`
  isolates CNY and USD ledgers.
- A trade is valid only when its account currency matches the security market:
  A-share markets use CNY and `US` uses USD. No implicit FX conversion is
  performed.
- Reuse the existing Decimal accounting, Lot, TradeMatch, Cycle, target,
  idempotency, and audit paths.

## 2. Account and API Contracts

- Add account list/create API contracts so a user can create or select a USD
  account explicitly. Account responses include `id`, `name`, `currency`, and
  `broker`.
- Extend security creation and trade validation to accept `US` and enforce the
  account/security currency matrix.
- Include `market` and `currency` in security, position, trade, lot, quote, and
  analytics responses where the frontend needs a display label.
- Keep money serialized as fixed-scale decimal strings. Currency is metadata,
  never a conversion instruction.

## 3. Market Data

- Add a market-calendar protocol with A-share and US implementations. US
  sessions use `America/New_York` regular hours (09:30–16:00) and injectable
  holiday sets for deterministic tests.
- Extend QuoteService and QuoteCollector to select the calendar by security
  market. Cache keys remain security IDs, so identical symbols across markets
  cannot collide.
- Add an optional US provider adapter behind the existing QuoteProvider
  protocol. The adapter is replaceable and tests use MockQuoteProvider; quote
  failure still returns the last cache row as stale.

## 4. Frontend

- Extend typed DTOs and stores with account currency, security market, and
  currency labels.
- The quick-trade selector displays `US:SYMBOL` and opens the existing buy/sell
  dialogs with a USD label. Existing A-share flows continue to show CNY.
- Format server-provided decimal strings with the selected currency symbol/code;
  never convert or recompute P&L in Vue.

## 5. Migration and Compatibility

- Existing `accounts.currency` values remain CNY. Existing securities remain
  A-share markets.
- Add only additive database/API fields or validation paths; no historical
  rows are rewritten.
- Existing callers that omit account ID continue using the default CNY account
  for A-share securities. USD trades require an explicit USD account.

## 6. Risks and Deferred Items

- US exchange holidays and provider licensing are kept behind injectable
  calendar/provider boundaries; the MVP does not promise exchange-grade
  real-time data.
- Runtime environments should provide IANA tzdata for correct US daylight
  saving transitions. The code keeps a fixed-offset fallback only so offline
  startup does not fail; that fallback is not suitable for exchange-grade
  timestamp accuracy.
- FX reporting, short selling, options, dividends, splits, and broker sync are
  explicitly deferred.
