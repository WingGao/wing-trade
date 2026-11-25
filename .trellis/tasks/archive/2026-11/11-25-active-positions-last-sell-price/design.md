# 技术设计：活动仓位与详情页增加上次卖出价并对齐双端展示

## 1. 架构与边界

本次改造遵循项目既有分层原则：**账务事实与基准派生值归后端，实时推流增量投影由前端 `BigInt` 精确十进制完成，展示逻辑通过共享 Vue 组件实现双端与双页统一**。

```
后端 (FastAPI + SQLAlchemy)
├── DashboardService._position_data()       ──▶ 新增 lastSellPrice / lastSellExecutedAt / vsLastSellPct
├── router.list_lots()                      ──▶ summary 新增 lastSellPrice / lastSellExecutedAt / vsLastSellPct
└── 共享查询辅助函数 _latest_active_sell()   ──▶ 统一按 (account_id, security_id, strategy_id) 查最近 ACTIVE SELL

前端 (Vue 3 + TypeScript + Pinia)
├── utils/valuation.ts                      ──▶ projectDashboardWithQuotes / projectLotsWithQuote 同步重算 vsLastSellPct
├── components/ActivePositionList.vue       ──▶ 双端统一活动仓位列表（筛选、搜索、紧凑模式、5维排序、3列布局）
├── components/SecurityDetailPanel.vue      ──▶ 可复用的个股详情核心面板（供首页 PC 右栏 & 独立详情页共用）
├── pages/DashboardPage.vue                 ──▶ PC 端左右分栏（左 ActivePositionList + 右 SecurityDetailPanel）；移动端直接渲染 ActivePositionList
└── pages/SecurityDetailPage.vue            ──▶ PC 端左 ActivePositionList + 右 SecurityDetailPanel；移动端顶部切换条 + SecurityDetailPanel
```

## 2. 后端数据契约与查询设计

### 2.1 最近一次有效卖出查询规则
- 复用已有联合索引 `ix_trades_scope_executed(account_id, security_id, strategy_id, executed_at)`，不修改表结构。
- 在 `backend/app/services/dashboard.py`（或抽取到 `AccountingService` / 独立辅助函数，供 `DashboardService` 与 `router.py` 共用）实现：
  ```python
  select(Trade)
  .where(
      Trade.account_id == account_id,
      Trade.security_id == security_id,
      Trade.strategy_id == strategy_id,
      Trade.side == TradeSide.SELL.value,
      Trade.status == TradeStatus.ACTIVE.value,
  )
  .order_by(Trade.executed_at.desc(), Trade.created_at.desc(), Trade.id.desc())
  .limit(1)
  ```
- **批量优化（首页）**：`DashboardService.build()` 可一次性查出当前 `account_id` 下所有 `ACTIVE` `SELL`（或限定 `security_id IN (...)`）并按 `(security_id, strategy_id)` 在内存中保留 `(executed_at, created_at, id)` 最大的一条，避免为每个持仓产生 N+1 SQL 查询。
- **相对涨跌幅公式**：
  - 在 `AccountingService` 中新增 `price_diff_pct(current_price, reference_price) -> Decimal | None`：
  - 当 `current_price` 与 `reference_price` 均非空且 `reference_price > 0` 时，返回 `round_price((current_price - reference_price) / reference_price * Decimal("100"))`，否则返回 `None`。

### 2.2 API Schema 扩展（纯向后兼容新增字段）
- `backend/app/api/schemas/dashboard.py` -> `DashboardPositionRead` 新增：
  - `last_sell_price: Decimal | None = None`（序列化 `lastSellPrice`）
  - `last_sell_executed_at: datetime | None = None`（序列化 `lastSellExecutedAt`，北京时间 ISO）
  - `vs_last_sell_pct: Decimal | None = None`（序列化 `vsLastSellPct`）
- `backend/app/api/v1/router.py` -> `GET /api/v1/securities/{security_id}/lots` 返回的 `summary` 新增：
  - `lastSellPrice: str | None`（6 位小数字符串）
  - `lastSellExecutedAt: str | None`（北京时间 ISO）
  - `vsLastSellPct: str | None`（6 位小数字符串）

## 3. 前端类型与实时推流派生

### 3.1 `frontend/src/api/types.ts`
- `DashboardPosition` 新增可选字段：`lastSellPrice?: string | null`、`lastSellExecutedAt?: string | null`、`vsLastSellPct?: string | null`。
- `LotsSummary` 新增可选字段：`lastSellPrice?: string | null`、`lastSellExecutedAt?: string | null`、`vsLastSellPct?: string | null`。

### 3.2 `frontend/src/utils/valuation.ts`
- 新增 `computePriceDiffPct(current: DecimalValue | null, reference: DecimalValue | null): string | null`：
  - 基于现有 `subDecimal` + `computeReturnPct(subDecimal(current, reference), reference)` 实现，严格使用 `BigInt` 与 `ROUND_HALF_UP`，与后端 `AccountingService.price_diff_pct` 保持完全一致。
- `projectDashboardWithQuotes()`：当仓位报价更新时，同步重算 `vsLastSellPct = computePriceDiffPct(price, parseDecimal(pos.lastSellPrice))`。
- `projectLotsWithQuote()`：当详情页报价更新时，同步重算 `summary.vsLastSellPct = computePriceDiffPct(price, parseDecimal(summary.lastSellPrice))`。

## 4. 前端核心组件设计

### 4.1 共享活动仓位列表 `frontend/src/components/ActivePositionList.vue`
- **Props**：
  - `positions: DashboardPosition[]`
  - `totalOpenPositions: number`
  - `currency: Currency | null`
  - `loading?: boolean`
  - `activeKey?: string | null`（当前选中持仓键 `${securityId}|${strategyId}`，PC 端高亮当前右侧详情对应行）
  - `pendingScopes?: PositionRefreshScope[]`、`unlistedScopes?: PositionRefreshScope[]`
  - `compactDefault?: boolean`
- **Emits**：
  - `select(position: DashboardPosition)`：点击行时触发（PC 首页切换右侧详情；PC/移动端独立详情页切换证券；移动端首页跳转详情页）
  - `recalculate(scope: PositionRefreshScope)`：触发单行或无活动仓位范围的待刷新重算
- **内部状态与计算**：
  - `filterTab: 'all' | 'lowestProfit' | 'belowLastSell' | 'loss'`（默认 `'all'`）
  - `keyword: string`（按 `symbol` / `market:symbol` / `name` 忽略大小写过滤）
  - `compactMode: boolean`（点击右上角 `紧凑` 按钮切换）
  - `sortField: 'marketValue' | 'unrealizedPnl' | 'changePct' | 'lowestReturnPct' | 'vsLastSellPct'`（默认 `'changePct'`）
  - `sortOrder: 'descending' | 'ascending'`（默认 `'descending'`）
  - 筛选计数始终基于当前账户全量 `positions` 计算（不受搜索词影响，搜索框与筛选标签做交集过滤，确保标签角标清晰稳定）。
  - 排序函数 `sortValue(position, field)` 返回 `number | null`，无效值始终置后。
- **响应式与零溢出策略**：
  - 三列网格使用 `minmax(0, ...)` 与等宽数字 `.num`，右列价格三行采用固定内部 3 列网格（标签 | 价格 | 涨跌幅）保证垂直对齐。
  - 在移动端（`<= 768px`）自动收紧内边距与字号，筛选栏支持换行或横向紧排且不触发整页横向滚动（`scrollWidth <= innerWidth`）。
  - 报价异常状态（已过期 / 估算 / 盘前 / 盘后 / 不可用）以极简微标签或悬停 tooltip 展示在「现价」行旁，不破坏三行对齐。

### 4.2 可复用个股详情面板 `frontend/src/components/SecurityDetailPanel.vue`
- 将当前 `SecurityDetailPage.vue` 中与“单只证券详情”强相关的全部逻辑（证券加载、账户/策略匹配、SSE 临时订阅、3 秒降级轮询、`intraday` + `lots` 串行加载与代次防护、买卖/止盈/跟踪标的弹窗、重算防重、分时图、持仓汇总、批次表、已结清买卖明细）内聚到 `SecurityDetailPanel.vue`。
- **Props**：
  - `market: string`
  - `symbol: string`
  - `accountId?: string`
  - `strategyId?: string`
  - `embedded?: boolean`（`true` 时表示内嵌在首页 PC 右栏：显示“打开独立详情页”快捷按钮、标题降级为 `<h3>` 避免与首页 `<h2>` 冲突、账户选择器隐藏以跟随首页当前账户）
- **Emits**：
  - `mutated()`：买卖记账、止盈保存、重算或跟踪标的修改后通知父容器（首页收到后刷新 Dashboard 快照，保持左栏与右栏账务一致）
- **持仓汇总区升级**：
  - 在原有 4 项（总持股数/总份额、持仓成本、已实现收益、总浮盈）基础上新增第 5 项 **上次卖出价**：
    - 主值：`displayHoldingSummary.lastSellPrice`（按证券小数位格式化，无则显示 `暂无卖出`）
    - 副信息：有值时展示相对现价涨跌幅（`较上卖 +x.xx% / -x.xx%`，红涨绿跌）及成交日期；无行情时涨跌幅显示 `—`。
  - 桌面端 `.holding-summary` 调整为 5 列（窄桌面自适应折行），移动端为 2 列网格（第 5 项跨两列或紧凑排版，零溢出）。

### 4.3 页面组装与联动
- **`DashboardPage.vue`**：
  - **PC 端（`!isMobile`）**：
    - 顶部页头、估值提示条、快速记账卡片、账户 5 项指标卡片保持清晰结构。
    - 活动仓位区块升级为双栏工作台（`.dashboard-workspace`）：
      - 左栏（约 `500px ~ 560px` 或 `minmax(440px, 44%)`）：`ActivePositionList`，选中状态与 `selectedPosition` 双向同步；首次加载或账户切换后自动选中排序第一只持仓。
      - 右栏（`1fr`，`min-width: 0`）：`SecurityDetailPanel`（`:embedded="true"`），传入选中仓位的 `market`、`symbol`、`accountId`、`strategyId`；监听 `@mutated` 刷新首页快照。
    - 自选股行情卡片保留在工作台下方。
  - **移动端（`isMobile`）**：
    - 保留原有顶部工具条、汇总网格、快速记账。
    - 活动仓位区块直接渲染同一 `ActivePositionList`（点击行跳转到 `/security/:market/:symbol`）。
- **`SecurityDetailPage.vue`**：
  - **PC 端（`!isMobile`）**：
    - 左侧保留/升级为 `ActivePositionList`（宽度适配三列布局，点击行通过 `router.push` 切换证券，高亮当前路由证券）。
    - 右侧直接渲染 `SecurityDetailPanel`（`:embedded="false"`）。
  - **移动端（`isMobile`）**：
    - 保留吸顶 `.m-pos-strip` 活动仓位快速切换条（并在卡片副行或状态点体现上卖对比信息），下方渲染 `SecurityDetailPanel`。

## 5. 兼容性、风险与回滚

- **向后兼容**：所有后端新增字段均允许为 `null` 且为纯追加字段，旧客户端/旧测试不会因缺字段报错。
- **E2E 测试影响**：
  - 首页 PC 端由 `el-table` 改为统一三列组件 + 右栏详情面板后，原有依赖 `el-table` 表头文本（如“最低价批次合计”、“策略回本参考价”表头）的 Playwright 用例需要同步更新以匹配新 UI 契约，并新增上次卖出价、筛选、5 维排序与 PC 右栏联动断言。
  - 需同步更新 `.trellis/spec/frontend/active-position-dashboard.md` 与 `mobile-high-density.md`、`portfolio-detail-guidelines.md`。
- **回滚策略**：无数据库迁移，全部变更为代码层新增与组件重构，若需回滚可直接按 Git 提交原子回退。
