# M1 前端工作台与容器化部署 (m1-frontend-mvp) — Implementation Plan

## 1. Prerequisite

- 必须先完成并通过 `11-05-m0-ledger-api` 与 `11-05-m1-market-quote`。

## 2. Ordered Checklist

- [ ] **Step 1: 初始化 `frontend/` (Vue 3 + TS + Vite + Element Plus + Pinia + Vue Router) 与 API 类型客户端**
- [ ] **Step 2: 实现 `/dashboard` 总览页、自选股行情组件 (`QuoteBadge`) 与隐藏已结清全局/局部切换**
- [ ] **Step 3: 实现 `/security/:market/:symbol` 个股详情页、批次表 (`LotTable`)、买入弹窗 (`BuyTradeDialog`)、卖出配对抽屉 (`SellMatchDrawer`) 与止盈设置弹窗 (`ProfitTargetDialog`)**
- [ ] **Step 4: 实现 `/trades` 交易流水（含撤销确认与审计记录查看）与 `/analytics` 复盘页**
- [ ] **Step 5: 编写 `compose.yaml`、前后端 `Dockerfile`、`README.md` 及 Playwright E2E 测试 (`e2e/mvp_ledger_flow.spec.ts`)**

## 3. Validation Commands

```bash
cd frontend
pnpm run type-check
pnpm run build
npx playwright test
```
