# Frontend State Management

## State Categories

Pinia stores own reusable server and preference state. Local refs belong in a
page or dialog when no other component needs them. Route params are URL state.
The current stores are ledger, quotes, and preferences.

## Store Rules

The ledger store loads positions, analytics summary, and trades. The quotes store
owns quote loading, refresh state, and lookup by security ID. The preferences
store owns includeClosed and display color preferences. Keep exact monetary
strings from the API in state; do not normalize them through Number for requests.

## Server State

Use the typed API client for reads and mutations. Pages call store load methods
in onMounted and reload after a successful mutation. Quote load errors are
swallowed or shown as stale state so ledger actions remain available.

## Derived State

Use computed values for filtered rows and display-only labels. A derived value
must point back to server state and must not reimplement accounting or matching.
