# 前端移动端高信息密度适配技术设计

## 1. 架构决策

采用**同仓「共享业务逻辑 + 视口分端视图」**方案，而非另起前端项目或纯 CSS 缩窄桌面表格：

- **逻辑层完全复用**：继续共用 `src/api/`、`src/stores/`、`src/utils/` 及各页面 `<script setup>` 中的轮询代次控制、并发锁、估值降级、待刷新 scope 合并与排序计算。
- **视口判断统一收口**：新增 `src/utils/viewport.ts`（导出 `useViewport()` composable，以 `max-width: 768px` 为断点，基于 `window.matchMedia` 响应式更新 `isMobile`）。
- **视图层按端渲染**：
  - 桌面端（`!isMobile`）保持原有 Element Plus 侧边栏、`el-table`、`el-card` 布局与 DOM 结构不变，确保桌面端与现有 E2E 测试零破坏；
  - 移动端（`isMobile`）渲染专为竖屏设计的紧凑原生网格/列表结构（`.m-*` 样式体系），彻底避开 `el-table` 横向溢出和 `el-card` 宽大留白。

## 2. 视觉与高密度样式规范（`src/styles.css`）

1. **移除硬编码最小宽度**：
   - 将 `body { min-width: 1080px; }` 移入 `@media (min-width: 769px)`，移动端 `body` 允许 `min-width: 0; overflow-x: hidden;`。
2. **数字对齐与涨跌语义**：
   - 全站数字类（`.num`、金额、涨跌幅、股数）统一 `font-variant-numeric: tabular-nums;`，沿用红涨（`#e34d3c`）/ 绿跌（`#199868`）/ 灰平（`#657287`）。
3. **移动端全局外壳（`App.vue`）**：
   - 顶栏 `.m-topbar`：高度 `44px`，`position: sticky; top: 0; z-index: 20`，左侧品牌标识 + 当前页标题，右侧用户名缩写/退出按钮。
   - 内容区 `.m-main`：内边距 `8px 8px calc(60px + env(safe-area-inset-bottom))`，底部为 TabBar 预留安全间距。
   - 底部导航 `.m-tabbar`：高度 `50px + env(safe-area-inset-bottom)`，`position: fixed; bottom: 0`，3 个等宽入口：总览（`/dashboard`）、流水（`/trades`）、复盘（`/analytics`）。

## 3. 逐页面与组件设计

### 3.1 总览页（`DashboardPage.vue`）
- **顶部工具栏**：单行紧凑排布（估值状态 `el-tag` + 刷新按钮 + 全局止盈按钮 + 显示已结清开关），估值状态提示条压缩为紧凑小字条。
- **账户汇总（`.m-summary-grid`）**：
  - 上排 2 列：持仓总价值（大号粗体 + 状态小字）、账户未实现浮盈（红绿大号粗体 + 收益率）；
  - 下排 3 列：累计买入、累计卖出、已实现收益（紧凑小号等宽数字）。
- **快速记账（`.m-quick-trade`）**：
  - 默认仅占 1 行：搜索框 + 「查找」按钮；
  - 存在搜索结果 / 无结果新增 / 已选中证券时，在下方紧凑展开候选列表、账户选择、买入记账、卖出配对或创建币种账户按钮，复用现有 `BuyTradeDialog` 与 `SellMatchDrawer`。
- **活动仓位（`.m-position-list`，全展开模式）**：
  - 列表头（3 列对齐，中间列支持点击切换 `quote.changePct` 降序/升序，带方向箭头指示）：
    - 左（`minmax(0, 1.15fr)`）：`证券 / 持仓`
    - 中（`minmax(0, 1.05fr)`，右对齐）：`当前总价值 ↕`
    - 右（`minmax(0, 0.95fr)`，右对齐）：`持仓浮盈`
  - 每行主区（3 列）：
    - 左：证券代码 + 名称（点击跳转个股详情）、`数量股 · 均价`
    - 中：复用 `QuoteBadge`（传入 `displayValue` 与 `compact`）紧凑展示总价值 + 现价/涨幅/状态标签（点击/聚焦仍可查看报价更新信息）
    - 右：浮盈金额（粗体红绿）+ 收益率
  - 每行底栏（1 行紧凑次级条，`11px`，浅灰底或顶部分割线）：
    - 左侧：`最低批 买入价 · x批 x股 金额 · 组浮盈/率`（无则显示 `无最低批`）；
    - 右侧：`回本 策略回本参考价`；若 `row.needsRefresh` 则额外展示醒目「刷新持仓」按钮。
  - 无活动仓位但存在 `unlistedScopes` 时，以紧凑告警行展示「刷新持仓」入口。
- **自选股行情**：2 列紧凑卡片网格（`.m-quote-grid`）。

### 3.2 个股详情页（`SecurityDetailPage.vue`、`LotTable.vue`、`SettledTradeTable.vue`、`IntradayChart.vue`）
- **头部**：证券代码与名称 + 实时报价 `QuoteBadge`；下方一行紧凑操作栏（账户选择 + 录入买入 + 卖出配对 + 全局/个股止盈）。
- **分时图（`IntradayChart.vue`）**：移动端压缩容器最小高度至 `160px`，SVG 保持 `viewBox` 等比缩放不溢出。
- **持仓汇总**：复用现有 `.holding-summary` 2x2 网格，在 `<= 768px` 下进一步压缩 padding（`8px 10px`）和字号（`15px`）。
- **持仓批次（`LotTable.vue`）**：
  - 移动端渲染 `.m-lot-list`（沿用 `sortedLots` 倒序）：
    - 第 1 行：短 ID（`lot.id.slice(0, 8)`）+ 状态标签 + 买入时间 + 右侧「设置」按钮（仅 `OPEN` 显示）；
    - 第 2 行（3 列）：`剩余/初始股数` | `含费成本` | `浮盈 / 收益率`（已结清显示 `—`）；
    - 第 3 行：止盈目标文本（批次设置或继承来源，未设置显示 `未设置`）。
- **已结清买卖明细（`SettledTradeTable.vue`）**：
  - 移动端渲染 `.m-settled-list`（沿用 `sortedDetails` 倒序）：
    - 第 1 行：方向标签 + 批次短 ID + 成交时间；
    - 第 2 行（3 列）：`成交价 / 本批数量`（卖出附带整笔量小字）| `买入金额 / 净回款` | `已实现盈亏`。

### 3.3 交易流水页（`TradesPage.vue`）与复盘分析页（`AnalyticsPage.vue`）
- **交易流水**：移动端渲染紧凑筛选条 + `.m-trade-list`：
  - 第 1 行：方向标签 + 证券代码/名称 + 成交时间 + 状态；
  - 第 2 行：`数量 × 价格`（附带费用小字）+ 右侧「编辑」「撤销」按钮（沿用 `canEdit` 与 `ACTIVE` 禁用逻辑）。
- **复盘分析**：移动端 2x2 紧凑指标格 + `.m-cycle-list`（证券/策略/状态/起止时间）。

### 3.4 业务弹窗、抽屉与登录页
- **`BuyTradeDialog.vue`、`EditTradeDialog.vue`、`ProfitSettingDialog.vue`、`ProfitTargetDialog.vue`**：
  - 移动端 `width` 设为 `94vw`，`el-form` 切换为 `label-position="top"`（桌面端维持原 `label-width`）；
  - 日期选择器等控件在移动端设为 `width: 100%`。
- **`SellMatchDrawer.vue`**：
  - 移动端 `size="100%"`，表单 `label-position="top"`；
  - 预览汇总压缩为 3 列紧凑行；
  - 配对明细在移动端渲染紧凑列表（买入时间、原始价、本次配对量/可用量/批次短 ID、分摊成本），替代宽表避免横向滚动。
- **`LoginPage.vue`**：
  - 移动端 `.auth-screen` 减少外边距至 `12px`，`.code-row` 确保验证码输入框与按钮在 360px 宽度下不挤压溢出。

## 4. 兼容性与风险控制

1. **现有契约保护**：
   - `active-position-dashboard.md` 与 `portfolio-detail-guidelines.md` 中所有桌面端 Playwright 断言（表头文本、最低价批次 DOM 文本、hover 提示、排序等）均运行在默认桌面视口（1280x720），由于桌面端分支保持原样，不会破坏既有测试。
   - 移动端同样遵守全部业务契约：不计算账务、保留 stale 文本、卖出先预览、编辑保留原始字符串精度。
2. **报价提示交互**：
   - `QuoteBadge.vue` 紧凑模式下 `el-tooltip` 在手机端通过点击触发（`tabindex="0"` + Element Plus 默认触控支持），确保手机端点击“实时/已过期”标签也能看到来源与时间。

## 5. 验证策略

1. `pnpm run type-check`（`vue-tsc --noEmit`）无报错；
2. `pnpm run build` 构建成功；
3. 运行全部现有 Playwright E2E 用例（桌面端回归）；
4. 新增 `frontend/e2e/mobile_layout.spec.ts`（使用 mocked API，不碰本地真实 `ledger.db`），在 `390x844` 和 `360x780` 视口下验证：
   - 底部 TabBar 导航切换；
   - 总览页零横向溢出、活动仓位全展开展示（含最低批与回本参考价）及涨幅排序切换；
   - 快速记账查找证券 -> 打开买入/卖出抽屉并完成预览与提交（零横向溢出）；
   - 个股详情页分时图、4 项汇总、持仓批次、已结清明细及止盈弹窗（零横向溢出）；
   - 交易流水页列表与编辑弹窗、复盘分析页（零横向溢出）。
