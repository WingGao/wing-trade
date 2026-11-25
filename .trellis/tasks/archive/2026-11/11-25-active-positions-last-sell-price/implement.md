# 执行计划：活动仓位与详情页增加上次卖出价并对齐双端展示

## 1. 有序实施清单

### 阶段一：后端上次卖出价支持与测试
1. **领域公式扩展**：在 `backend/app/services/accounting.py` 新增 `AccountingService.price_diff_pct(current_price, reference_price)`，提供精确 `Decimal` 百分比计算并补充中文注释。
2. **Dashboard 接口扩展**：
   - 更新 `backend/app/api/schemas/dashboard.py` 的 `DashboardPositionRead`，新增 `last_sell_price`、`last_sell_executed_at`、`vs_last_sell_pct`。
   - 更新 `backend/app/services/dashboard.py`，在 `build()` 中批量查询当前账户各 `(security_id, strategy_id)` 最近一笔 `ACTIVE` `SELL`（按 `executed_at DESC, created_at DESC, id DESC`），并在 `_position_data()` 中输出 `lastSellPrice`、`lastSellExecutedAt`、`vsLastSellPct`。
3. **详情页 Lots 接口扩展**：
   - 更新 `backend/app/api/v1/router.py` 的 `list_lots()`，查询当前范围最近一笔 `ACTIVE` `SELL`，在 `summary` 中输出 `lastSellPrice`、`lastSellExecutedAt`、`vsLastSellPct`。
4. **后端单测与集成测试**：
   - 补充 `backend/tests/unit/test_accounting.py`（`price_diff_pct` 正常/零值/空值测试）。
   - 补充 `backend/tests/integration/test_dashboard_api.py` 与 `test_dashboard_service.py`、`test_portfolio_lots.py`（跨周期最近有效卖出、撤销后回退上一笔、无卖出返回 `null`、缺行情 `vsLastSellPct` 为 `null`）。

### 阶段二：前端类型与实时推流派生
5. **类型定义**：更新 `frontend/src/api/types.ts` 中的 `DashboardPosition` 与 `LotsSummary`。
6. **精确派生**：更新 `frontend/src/utils/valuation.ts`，新增 `computePriceDiffPct()`，并在 `projectDashboardWithQuotes()` 与 `projectLotsWithQuote()` 中同步重算 `vsLastSellPct`。
7. **前端工具单测**：如已有 valuation 单测则补充用例，验证实时推流更新现价时 `vsLastSellPct` 与后端结果一致。

### 阶段三：共享组件抽取与页面升级
8. **创建 `frontend/src/components/ActivePositionList.vue`**：
   - 实现与 `docs/image.png` 对齐的标题栏（含紧凑模式切换）、4 个筛选标签（带数量角标）、代码/名称搜索框、3 列 5 维正反排序表头、三列高密度行结构与待刷新入口。
   - 确保 PC 端与移动端样式适配，移动端零横向溢出。
9. **抽取 `frontend/src/components/SecurityDetailPanel.vue`**：
   - 迁移 `SecurityDetailPage.vue` 的核心详情逻辑（保留完整代次防护、SSE 订阅、3 秒降级轮询、并发锁与弹窗交互）。
   - 持仓汇总区新增“上次卖出价”指标项（含较上卖涨跌幅与无卖出空态）。
   - 支持 `embedded` 模式（供首页 PC 右栏嵌入，提供“独立页打开”入口并触发 `mutated` 事件）。
10. **重构 `frontend/src/pages/DashboardPage.vue`**：
    - PC 端将活动仓位区块升级为左右分栏工作台（左 `ActivePositionList` + 右 `SecurityDetailPanel`），默认选中排序第一只持仓，支持点击切换与写入后双栏同步刷新。
    - 移动端直接复用 `ActivePositionList`，点击跳转独立详情页。
11. **重构 `frontend/src/pages/SecurityDetailPage.vue`**：
    - PC 端左侧升级为 `ActivePositionList`，右侧使用 `SecurityDetailPanel`。
    - 移动端保留吸顶切换条并使用 `SecurityDetailPanel`。
12. **样式微调**：检查 `frontend/src/styles.css`，确保 PC 端分栏在 `900px ~ 1440px+` 宽度下布局舒适、移动端 `360px ~ 430px` 零横向溢出。

### 阶段四：测试验证与规范更新
13. **更新与新增前端 E2E 测试**：
    - 更新受活动仓位 UI 变更影响的现有 Playwright 测试，确保全部使用 mocked API（绝不写入本地 `ledger.db`）。
    - 新增覆盖：上次卖出价展示与无卖出空态、4 个筛选标签计数与过滤、搜索过滤、紧凑切换、5 个表头字段正反排序及缺值置后、PC 端首页右栏详情联动切换、独立详情页上次卖出价展示、移动端 `360x780` / `390x844` 零横向溢出。
14. **更新 Trellis Spec**：按 Phase 3.3 同步更新 `.trellis/spec/frontend/active-position-dashboard.md`、`mobile-high-density.md`、`portfolio-detail-guidelines.md`。

## 2. 验证命令

```bash
# 后端检查与测试
cd backend && uv run ruff check . && uv run pytest

# 前端类型检查、构建与 E2E 测试
cd frontend && pnpm run type-check && pnpm run build && pnpm run test:e2e
```

## 3. 高风险文件与回滚点

- **高风险文件**：
  - `frontend/src/pages/DashboardPage.vue`（轮询代次、账户切换、写入并发锁与右栏详情联动状态需避免竞态）
  - `frontend/src/components/SecurityDetailPanel.vue`（从 `SecurityDetailPage.vue` 抽取时必须完整保留 `generation` / `lotGeneration` / `dataGeneration` / `quotesStore.watchSymbol` 释放逻辑）
  - `frontend/src/utils/valuation.ts`（`BigInt` 十进制精度需与后端 `Decimal` 完全对齐）
- **回滚点**：
  - 后端提交完成后先跑通后端 `pytest`，再进行前端改造；
  - 本次无数据库 Schema 变更，所有改动均可通过 Git 干净回退，且严格禁止修改或清空本地 `ledger.db`。
