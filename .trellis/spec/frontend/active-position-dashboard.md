# 活动仓位展示与排序契约

## 1. 适用范围

适用于双端共享的活动仓位列表组件 `ActivePositionList.vue`（用于首页 `DashboardPage.vue` PC/移动端以及独立详情页 `SecurityDetailPage.vue` PC 左侧切换栏），以及 PC 端首页右侧联动的 `SecurityDetailPanel.vue`。

## 2. 签名

- `ActivePositionList` 接收 `positions: DashboardPosition[]`、`totalOpenPositions: number`、`currency: Currency | null`、`accountId?: string`、`loading?: boolean`、`activeKey?: string | null`、`pendingScopes?: PositionRefreshScope[]`、`unlistedScopes?: PositionRefreshScope[]`，发出 `select(position)` 与 `recalculate(scope)` 事件。
- 后端 `GET /api/v1/dashboard` 与 `GET /api/v1/securities/{id}/lots`（`summary`）均提供 `lastSellPrice`、`lastSellExecutedAt`、`vsLastSellPct`；口径为当前账户 + 证券 + 策略下全部历史有效卖出（`ACTIVE` `SELL`）的最近一笔（跨持仓周期）。
- `valuation.ts` 的 `projectDashboardWithQuotes()` 与 `projectLotsWithQuote()` 在 SSE 实时推流更新现价时使用 `BigInt` 精确十进制重算 `vsLastSellPct`。

## 3. 契约

- **双端统一三列布局**：左列为 `证券 / 持仓`（代码、名称、数量与加权均价），中列为 `总市值 / 浮盈`（总市值、总浮盈额与浮盈率、低批剩余数量与低批浮盈额），右列带左竖分割线并纵向对齐四行：`现价`（最新价 + 当日涨跌幅，悬停显示报价更新信息 tooltip）、`低批`（最低价批次买入价 + 相对涨跌幅）、`上卖`（上次卖出价 + 相对涨跌幅，无卖出显示 `暂无卖出` 与 `—`）、`20日振`（近 20 个已收盘交易日平均振幅，右对齐无符号中性色两位百分数，无数据或场外基金显示 `—`，悬停提示统计口径与截止日期）。
- **筛选与搜索**：顶部提供 `全部 / 低批盈利 / 低于上卖 / 浮亏仓位` 四个带数量角标的筛选标签（角标始终基于账户全量活动持仓计算）、本地 `代码 / 名称` 搜索框以及右上角 `紧凑` 视图切换按钮：
  - `低批盈利`：`lowestPriceGroup.unrealizedPnl > 0`；
  - `低于上卖`：同时满足 `vsLastSellPct < 0`（有效现价低于上次卖出价）**且** `lastSellPrice < lowestPriceGroup.purchasePrice`（上次卖出价严格小于低批买入价），任一数值缺失或非有限数字均不命中；
  - `浮亏仓位`：`unrealizedPnl < 0`。
- **五维正反排序**：默认按「现价（当日涨跌幅 `quote.changePct`）」降序；表头「总市值」「浮盈」「现价」「低批」「上卖」5 个标题均可独立点击正反切换排序（无“取消排序”第三态，**表头不增加振幅排序**），任意排序维度与方向下缺失值始终排在末尾。
- **PC 端主从联动与 URL Hash 导航**：PC 端首页右侧空余区域直接内嵌 `SecurityDetailPanel` 展示当前选中仓位的个股详情，选中态以 `route.hash` 为单一事实源（格式 `#MARKET:SYMBOL`，同证券多策略时为 `#MARKET:SYMBOL@<strategyId>`）；未带 hash 或 hash 未命中当前账户仓位时默认回退排序第一只持仓。点击其他仓位使用 `router.push` 写入 hash 以支持浏览器前进/后退；点击当前已展示的默认仓位或切换全局账户清空 hash 时使用 `router.replace`，避免产生无效后退步；右侧写入操作完成后同步刷新左侧仓位快照。

## 4. 校验与状态矩阵

| 条件 | 界面行为 |
|---|---|
| 行情有效 | 显示实时总价值、三位现价、涨幅和“实时”标签 |
| 行情过期 | 保留参考总价值与现价，状态显示“已过期”，提示中保留报价来源和时间 |
| `quote=null` 或 `lastPrice=null` | 状态显示“不可用”，总价值不得补 0 |
| `changePct=null` 或非数字 | 该行不参与涨幅大小比较，固定在有效行之后 |
| 默认排序 | 按 `changePct` 降序 |
| 点击列头 | 在升序和降序之间切换，不取消涨幅排序 |
| 数据刷新 | 新快照仍沿用当前排序方向，不恢复后端原始顺序 |
| `vsLastSellPct < 0` 且 `lastSellPrice < lowestPriceGroup.purchasePrice` | 命中“低于上卖”筛选条件并计入角标 |
| `vsLastSellPct < 0` 但 `lastSellPrice >= lowestPriceGroup.purchasePrice`（或缺失任一价格） | 不命中“低于上卖”筛选条件 |
| PC 端点击左侧其他仓位 | `router.push` 写入 `#MARKET:SYMBOL`（多策略附 `@strategyId`），右栏切换并可浏览器前进/后退 |
| 直接访问 `/dashboard#MARKET:SYMBOL` | 数据加载后自动定位并高亮对应仓位；未命中或格式非法时回退默认排序首项 |
| 切换全局账户 | 若当前存在 hash，使用 `router.replace` 清空旧账户残留 hash |

## 5. 正常、边界与错误示例

- 正常：持仓 `600` 股、现价 `7.890`、涨幅 `+2.50%`，第一行显示总价值，第二行显示 `¥ 7.890  +2.50%  实时`。
- 边界：涨幅为 `0` 的持仓仍属于有效值，应先于缺行情持仓；缺行情持仓始终最后。
- 边界：总价值为 `0` 时显示 `¥ 0.00`，不能显示破折号；`lastPrice=null` 才显示不可用状态。
- 边界：“低于上卖”需同时满足 `现价 < 上卖` 与 `上卖 < 低批`（例如现价 `71.45`、上卖 `75.80`、低批 `76.50` 命中；现价 `248.60`、上卖 `264.50`、低批 `242.00` 因上卖不小于低批不命中；`lastSellPrice === lowestPriceGroup.purchasePrice` 或缺低批/缺上卖均不命中）。
- 错误：点击列头后出现第三种“取消排序”状态，导致刷新后顺序不稳定。
- 错误：为了显示总价值把格式化后的金额字符串转换后回写持仓成本。

## 6. 必须测试

- Playwright 断言两个表头为“持仓”“当前总价值”。
- Playwright 断言总价值与现价位于不同行，第二行同时包含现价、涨幅和“实时”标签。
- hover 状态标签后出现“报价更新信息”，并包含行情来源及报价/获取时间。
- 使用至少三条持仓验证默认降序、点击列头升序，以及缺行情持仓在两种方向下均位于末尾。
- 验证最低价批次第二行 DOM 文本为 `2批150股1,200.00`，视觉间距由 CSS 提供。
- `active_positions_last_sell.spec.ts` 断言“低于上卖”筛选及角标同时校验 `vsLastSellPct < 0` 与 `lastSellPrice < lowestPriceGroup.purchasePrice`（覆盖命中项与仅 `现价 < 上卖` 但 `上卖 >= 低批` 的排除项）。
- `active_positions_last_sell.spec.ts` 断言 PC 端点击仓位后 URL 写入 `#MARKET:SYMBOL`，浏览器 `goBack()` / `goForward()` 可准确切换右栏股票，且直接访问 `/dashboard#MARKET:SYMBOL` 可自动选中对应仓位。
- 运行 `pnpm run type-check`、相关 Playwright 用例和 `pnpm run build`。

## 7. 错误与正确写法

```ts
// 错误：缺行情时用 0 参与排序，导致不可用持仓混入有效涨幅。
const change = Number(position.quote?.changePct ?? 0)
```

```ts
// 正确：无效涨幅显式返回 null，并在比较器中将无效行固定放在末尾。
function positionChangeValue(position: DashboardPosition): number | null {
  const value = Number(position.quote?.changePct)
  return Number.isFinite(value) ? value : null
}
```

```vue
<!-- 错误：活动仓位把报价来源和时间常驻铺在价格下方。 -->
<QuoteBadge :quote="row.quote" />

<!-- 正确：传入总价值与紧凑模式，由 QuoteBadge 统一处理两行展示和 hover 提示。 -->
<QuoteBadge :quote="row.quote" :display-value="currentPositionValue(row)" compact />
```

```ts
// 错误：“低于上卖”仅判断 vsLastSellPct < 0，会把上卖价高于低批买入价的仓位也筛进来。
function isBelowLastSell(position: DashboardPosition): boolean {
  const diffPct = parseFiniteNumber(position.vsLastSellPct)
  return diffPct !== null && diffPct < 0
}
```

```ts
// 正确：“低于上卖”同时校验现价低于上次卖出价，且上次卖出价严格小于低批买入价。
function isBelowLastSell(position: DashboardPosition): boolean {
  const diffPct = parseFiniteNumber(position.vsLastSellPct)
  if (diffPct === null || diffPct >= 0) return false
  const lastSellPrice = parseFiniteNumber(position.lastSellPrice)
  const lowestPurchasePrice = parseFiniteNumber(position.lowestPriceGroup?.purchasePrice)
  return lastSellPrice !== null && lowestPurchasePrice !== null && lastSellPrice < lowestPurchasePrice
}
```
