# Frontend Hook Guidelines

## Current Pattern

This project uses Vue Composition API functions directly in script setup rather
than a separate composable directory. Use onMounted for initial page loading,
ref for local form state, computed for derived display values, and watch only
when a dependency change must reload data.

## Data Fetching

Call the typed functions in src/api/client.ts from a page or Pinia store. Catch
errors at a boundary that can show ElMessage or an ElAlert. Quote failures must
be isolated so ledger pages remain usable.

## Naming

Use verb-based async functions such as load, refresh, save, and submit. If a
shared composable is added later, name it useSomething and keep its return type
explicit.

## Common Mistakes

Do not duplicate fetch URL construction in components, start requests during
render, or treat a failed quote request as a failed ledger request.
