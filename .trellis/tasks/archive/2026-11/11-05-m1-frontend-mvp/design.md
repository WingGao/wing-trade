# M1 前端工作台与容器化部署 (m1-frontend-mvp) — Technical Design

## 1. Frontend Architecture

```text
frontend/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── playwright.config.ts
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── router/
│   │   └── index.ts             # /dashboard, /security/:market/:symbol, /trades, /analytics
│   ├── api/
│   │   ├── client.ts            # Axios/fetch wrapper with unified error toast & client_request_id
│   │   └── types.ts             # Strictly typed DTOs matching backend Pydantic schemas
│   ├── stores/
│   │   ├── ledger.ts            # Account, positions, summary state
│   │   ├── quotes.ts            # Quote polling & manual refresh store
│   │   └── preferences.ts       # includeClosed toggle, red-up/green-down color theme
│   ├── components/
│   │   ├── QuoteBadge.vue       # Price + change_pct + source_time + is_stale tag
│   │   ├── BuyTradeDialog.vue   # Buy trade form with fee breakdown
│   │   ├── SellMatchDrawer.vue  # Interactive FIFO / manual Lot allocation + live server preview
│   │   ├── ProfitTargetDialog.vue # Multi-tier target editor & conflict warning
│   │   └── LotTable.vue         # Active & settled lots table with expandable match history
│   └── pages/
│       ├── DashboardPage.vue
│       ├── SecurityDetailPage.vue
│       ├── TradesPage.vue
│       └── AnalyticsPage.vue
└── e2e/
    └── mvp_ledger_flow.spec.ts
```

## 2. Deployment & Persistence (`compose.yaml`)

- `backend` 容器：挂载宿主机 `./data:/app/data`，环境变量 `DATABASE_URL=sqlite:////app/data/ashare_journal.db`，启动时自动执行 `alembic upgrade head`。
- `frontend` 容器：Nginx 托管构建产物并将 `/api` 反向代理至 `backend:8000`。
