# Backend Logging Guidelines

## Scope

The FastAPI application uses Python logging. Request logging is installed in
app/main.py and emits structured fields through the logging extra mapping.

## Levels

Use INFO for completed requests and important state transitions, DEBUG for
diagnostic details when explicitly enabled, WARNING for recoverable provider
degradation, and ERROR with an exception for failed requests or unexpected
failures.

## Required Fields

Request logs include request_id, path, status_code, and duration_ms. Quote
degradation logs should identify the provider and stale-cache result without
logging request bodies or credentials.

## What to Log

Log request completion, uncaught request failures, provider failures, circuit
state changes, and identifiers needed to correlate a ledger audit event.

## What Not to Log

Do not log passwords, tokens, full request bodies, or arbitrary account data.
Amounts and symbols may be logged only when needed to diagnose a deterministic
ledger or quote calculation.

## Example

The middleware in app/main.py reads x-request-id, creates one when absent,
returns it in the response header, and passes it in logger extra fields.
