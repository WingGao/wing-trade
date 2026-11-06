# Frontend Directory Structure

## Layout

    frontend/src/main.ts             app bootstrap and Pinia registration
    frontend/src/App.vue              shell navigation and router outlet
    frontend/src/router/              dashboard, security, trades, analytics routes
    frontend/src/api/                 typed DTOs and fetch client
    frontend/src/stores/              ledger, quotes, and user preferences
    frontend/src/pages/               route-level page components
    frontend/src/components/          reusable dialogs, tables, and badges
    frontend/src/styles.css           shared visual tokens and layout styles
    frontend/e2e/                     Playwright acceptance tests
    frontend/Dockerfile               build and nginx packaging

## Organization

Pages own route-level loading and orchestration. Components own focused
presentation and emit saved/close events. Stores own reusable server loading and
preference state. Keep API field mapping in src/api/client.ts and types.ts.

## Naming

Use PascalCase for Vue component files, camelCase for functions and stores, and
lowercase route paths. Keep one page component per route.

## Examples

DashboardPage coordinates ledger and quotes stores. QuoteBadge and LotTable are
reusable display components, while SellMatchDrawer owns the sell preview form.
