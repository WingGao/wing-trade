# Quality Guidelines

## Overview

Backend accounting code is Decimal-only, deterministic, and test-first at the
domain boundary. Run `ruff check app tests`, `mypy app`, and `pytest -v` when the
development dependencies are available.

## Forbidden Patterns

- Do not accept binary `float`, `NaN`, or infinity in accounting inputs.
- Do not use SQLAlchemy `Float`/`REAL` for quantities, prices, fees, or P&L.
- Do not duplicate fees in both sell net and realized P&L.
- Do not silently return a negative breakeven price; expose recovered surplus
  separately.
- Do not mutate a lot or cycle without an `audit_events` row in the same
  transaction.

## Required Patterns

- Normalize quantity, price, and amount through `round_qty`, `round_price`, and
  `round_amount` before arithmetic or persistence.
- Keep pure matching/accounting functions independent from SQLAlchemy where
  possible; keep transaction ownership in `TradeLedgerService`.
- Preserve tail rounding by assigning the final sell-net residue to the last
  match.
- Prefer focused domain errors carrying a stable `code` and optional `details`.

## Testing Requirements

- Every accounting rule needs a deterministic unit test and boundary/error case.
- Randomized allocation tests must assert conservation, not implementation
  details.
- Integration tests cover ORM constraints, migrations, idempotency, voiding, and
  audit events.
- If a dependency is unavailable, report the exact skipped command; do not claim
  it passed.

## Code Review Checklist

- Decimal scales and rounding mode are explicit.
- No binary floating point enters a ledger calculation.
- FIFO ordering is stable and manual match scope is validated.
- Lot/Cycle/Trade state changes and voids are auditable.
- Migration upgrade and downgrade are reversible.
- Tests cover both normal and rejection paths.
