# Frontend Quality Guidelines

## Scope / Trigger

These rules apply to the Vue 3 + TypeScript + Pinia workbench and its API
boundary. The UI presents server-derived ledger values and must keep quote
failures separate from ledger failures.

## Required Patterns

- Keep REST DTOs in src/api/types.ts and use the shared client wrapper.
- Keep account, quote, and preference state in Pinia stores; pages coordinate
  stores and route navigation.
- Render quote source time and an explicit stale/unavailable state beside prices.
- Send sell previews to the server before submitting a sell and include a
  unique client request ID.
- Keep a visible quick trade entry on the dashboard: search a security first,
  then reuse BuyTradeDialog or SellMatchDrawer rather than duplicating trade
  forms in the page.
- Keep settled lots behind the includeClosed preference while preserving the
  server summary values.

## Forbidden Patterns

- Do not calculate ledger P&L or matching allocations in Vue components.
- Do not convert exact monetary strings to binary floating point for display
  calculations that affect a request.
- Do not hide quote errors by replacing prices with zero.
- Do not submit a sell without a preview or reuse a client request ID.

## Testing Requirements

- pnpm run type-check and pnpm run build must pass.
- Playwright covers navigation, offline quote rendering, settled-lot toggling,
  quick buy/sell entry after selecting a security, and the core buy/sell/target
  flow when a backend fixture is available.
- API client errors must leave ledger pages usable when the quote endpoint fails.

## Code Review Checklist

- Check route paths against the backend API aliases and response field names.
- Check that stale quote labels are visible and do not disable ledger actions.
- Check that all interactive dialogs expose loading and error feedback.
- Check Docker and nginx proxy paths before changing deployment files.
