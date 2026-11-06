# Frontend Type Safety

## Type Organization

Shared API DTOs live in src/api/types.ts and are imported with type-only imports
when possible. The client wrapper returns generic typed promises. Page-local
types are allowed for narrow UI state.

## Conventions

Use strict TypeScript and vue-tsc. Model decimal quantities, prices, IDs, and
timestamps as strings because the backend serializes exact Decimal values and
ISO timestamps. Use discriminated string unions for stable fields such as trade
side where practical.

## Runtime Boundaries

The API client is the boundary for unknown JSON. Keep response field names
aligned with the backend camelCase contract and keep request payloads explicit.
User input forms may use strings until submission.

## Forbidden Patterns

Do not use implicit any, duplicate DTO definitions in components, or convert
money to binary floating point for a request. Avoid unchecked casts when a typed
client method can express the response.

## Examples

Quote, Lot, Trade, and AnalyticsSummary are defined once in src/api/types.ts;
api.previewSell and api.sell use typed return values and explicit payloads.
