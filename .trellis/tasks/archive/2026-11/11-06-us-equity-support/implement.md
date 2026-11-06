# 美股支持 — Implementation Plan

## Prerequisites

- Preserve the existing A-share test suite and frontend quick-trade behavior.
- Confirm the final provider choice remains optional and does not become a
  ledger dependency.

## Ordered Checklist

- [x] Add market/currency domain constants and account/security currency
  validation, including USD account creation/list API and integration tests.
- [x] Extend API schemas and serializers with market, currency, and account
  context; reject cross-currency trades with a stable error code.
- [x] Add US trading calendar, provider adapter boundary, cache selection, and
  stale/fallback tests without changing A-share calendar behavior.
- [x] Extend quote/position/analytics responses and frontend DTOs/stores to
  display USD or CNY labels without conversion.
- [x] Update quick-trade selector and buy/sell dialogs for US securities and add
  Playwright coverage for a USD account flow.
- [x] Add migration/compatibility tests and update README/spec documentation.

## Validation Commands

```powershell
uv run --cache-dir .uv-cache --project backend --extra dev python -m pytest -q
uv run --cache-dir .uv-cache --project backend --extra dev python -m ruff check app tests
uv run --cache-dir .uv-cache --project backend --extra dev python -m mypy app
pnpm --dir frontend run type-check
pnpm --dir frontend run build
pnpm --dir frontend run test:e2e
docker compose config
```

## Risky Areas and Rollback

- Risky files: `backend/app/models/ledger.py`, `backend/app/api/deps.py`,
  `backend/app/api/v1/router.py`, `backend/app/market_data/`, and frontend API
  types/stores/dialogs.
- Keep schema changes in a new Alembic revision. Roll back by downgrading that
  revision and reverting the additive API/UI commit.
- If a US quote provider is unavailable, disable that provider and retain mock
  or cached stale responses; ledger writes must remain usable.
