# Backend API Error Handling

## Scenario: FastAPI `/api/v1` boundary

### 1. Scope / Trigger

This contract applies to FastAPI routes that call the ledger services and expose
domain failures to clients. It keeps error codes stable while allowing human
messages and diagnostic details to evolve.

### 2. Signatures

- Domain errors inherit `DomainError(message, details=None)` and expose a stable
  class attribute `code`.
- Route handlers raise domain errors and do not construct `JSONResponse`
  instances for business failures.
- `domain_error_handler(request, exc)` returns the standard error envelope.

### 3. Contracts

Every handled error is serialized as:

```json
{
  "error": {
    "code": "INSUFFICIENT_LOT_QTY",
    "message": "...",
    "details": {}
  }
}
```

Request validation errors use `code="VALIDATION_ERROR"`. Decimal ledger
inputs are parsed from strings or exact decimal values; binary JSON floats are
rejected by the shared API model validator.

### 4. Validation & Error Matrix

| Condition | Error code | HTTP status |
|---|---|---:|
| Missing or malformed request field | `VALIDATION_ERROR` | 422 |
| Non-positive quantity or price | `INVALID_QUANTITY` / `INVALID_PRICE` | 422 |
| Negative fee | `NEGATIVE_FEE` | 422 |
| Sell quantity exceeds open lots | `INSUFFICIENT_LOT_QTY` | 409 |
| Manual match has wrong scope or chronology | `LOT_OWNERSHIP_MISMATCH` / `FUTURE_LOT` | 422 |
| Voiding a buy with active matches | `LOT_HAS_ACTIVE_MATCHES` | 409 |
| Missing trade/entity | `TRADE_NOT_FOUND` / `NOT_FOUND` | 404 |
| Unique or other database conflict | `CONFLICT` | 409 |

### 5. Good / Base / Bad Cases

- Good: a rejected sell returns HTTP 409 and `error.code`, while the write
  transaction is rolled back.
- Base: a duplicate `(account_id, client_request_id)` returns the original
  trade and does not create a new error or ledger row.
- Bad: a route catches a `DomainError` and returns a success payload, or leaks a
  raw SQLAlchemy exception to the client.

### 6. Tests Required

- Integration tests assert the envelope and status for insufficient quantity,
  invalid manual matches, missing entities, and request validation.
- Idempotency tests assert the same trade ID and unchanged row count on retry.
- Transaction tests assert failed writes leave lots, trades, and audit events
  unchanged.

### 7. Wrong vs Correct

#### Wrong

```python
try:
    service.sell(...)
except Exception as exc:
    return {"error": str(exc)}
```

#### Correct

```python
service.sell(...)
# DomainError is handled centrally as:
# {"error": {"code": exc.code, "message": exc.message, "details": exc.details}}
```

Routes should let `DomainError` propagate to the centralized handler so status
mapping and response shape remain consistent.
