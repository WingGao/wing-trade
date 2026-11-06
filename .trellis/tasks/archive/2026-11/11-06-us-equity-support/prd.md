# 美股支持

## Goal

在现有 A 股账本与行情系统中增加美股普通股票支持，让用户可以用相同的批次、配对、止盈和行情工作流记录美股交易。

## Confirmed Repository Facts

- `Security` currently identifies an instrument by unique `market + symbol` and
  `Account` currently has one `currency` field defaulting to `CNY`.
- Trade and lot accounting already uses `Decimal`; fee fields are currently
  `commission`, `stamp_duty`, `transfer_fee`, and `other_fee`.
- The quote layer normalizes symbols to `MARKET:CODE`, has a trading calendar,
  stale cache, provider abstraction, and `/api/v1/quotes`.
- The frontend currently treats prices and summary amounts as server-formatted
  strings and has a dashboard quick-trade entry plus buy/sell dialogs.
- There is no FX rate table, currency conversion service, short-selling model,
  options model, dividend/corporate-action model, or broker integration.

## Confirmed Product Decisions

- USD uses a separate account/ledger. The MVP does not convert CNY and USD or
  aggregate their cash flows into one currency.
- US scope is long-only common stocks and ETFs. Short selling, options, and
  other derivatives are out of scope.
- Reuse the existing manual commission, stamp duty, transfer fee, and other fee
  fields as generic recorded fees. No US-specific regulatory fee calculator is
  added in this MVP.

## Recommended MVP Boundary

- Support long-only US common stocks and ETFs identified by `US:SYMBOL`, with
  USD-denominated accounts and no automatic CNY/USD conversion.
- Preserve the existing lot, matching, target, audit, and idempotency behavior.
- Add a US market calendar in `America/New_York`; keep A-share calendar behavior
  unchanged.
- Keep provider-specific quote acquisition behind `QuoteProvider`; do not make
  the MVP depend on a live US quote vendor for ledger correctness.

## Requirements

- **R-01 Instrument and account scope**: create and query US securities with a
  stable `US:SYMBOL` identity and make the account currency explicit in API and
  frontend responses.
- **R-02 Ledger compatibility**: buy, sell preview, manual/FIFO matching, void,
  targets, audit events, and idempotency work for USD accounts without binary
  floating point arithmetic.
- **R-03 US market timing**: use `America/New_York` trading sessions and mark
  quote cache freshness using the selected market calendar.
- **R-04 Quote display**: show market, currency, source time, fetched time, and
  stale state consistently for A-share and US quotes.
- **R-05 Frontend**: allow selecting a US security from the same quick-trade
  entry and show USD labels without changing A-share workflows.
- **R-06 Explicit exclusions**: no FX conversion, short selling, options,
  dividends, corporate actions, broker sync, or automatic orders in this MVP.

## Acceptance Criteria

- [x] A US security can be created, searched, selected from the dashboard, and
  used for a USD buy transaction that creates an open lot.
- [x] A US sell preview and submission preserve FIFO/manual matching,
  idempotency, audit events, and Decimal conservation invariants.
- [x] A-share and US positions can coexist without mixing their currencies or
  applying an implicit FX rate.
- [x] US quote snapshots use the US market timezone and expose stale state;
  quote failure does not block USD ledger operations.
- [x] Frontend buy/sell dialogs display USD for selected US instruments and keep
  CNY labels for A-share instruments.
- [x] Existing A-share backend tests, frontend type-check/build, and E2E tests
  remain passing.

