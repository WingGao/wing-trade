# 清仓关注与目标买回价提醒 — 技术设计文档（Design）

## 1. 架构边界与核心原则

- **关注与账务解耦**：持仓完全由 `Trade` / `Lot` 派生；关注是独立的账户级主观标记（`WatchlistItem`），不参与任何仓位成本、浮盈、回款或账户汇总计算。
- **后端接口分离、前端按开关融合**：
  - `GET /api/v1/dashboard` 保持现有契约不变（仍将 `WatchlistItem` 纳入 `_requested_securities` 以保障行情刷新）。
  - `GET /api/v1/watchlist` 独立返回当前账户关注列表及聚合只读指标。
  - 前端在视图层根据 `preferences.showWatchlist` 开关，将空仓关注项投影为列表行混入 `ActivePositionList.vue`，并对持仓中已关注标的叠加星标与买回价元信息。
- **复用现有行情与推送基础设施**：
  - 实时报价：复用 `MarketDataHub`（已订阅 `WatchlistItem`）+ 前端 `quotesStore` SSE 流。
  - 到价推送：在 `ProfitAlertEngine` 内扩展买回提醒评估分支，复用同一行情监听、防抖合并、交易日历判定、失败退避冷却与 `WeComWebhookChannel`。

---

## 2. 数据库与迁移设计

### 2.1 ORM 变更

1. **`WatchlistItem`（`backend/app/models/ledger.py`）**：
   - `target_buy_price: Mapped[Decimal | None] = mapped_column(ExactDecimal(6))`
   - `alert_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)`
   - `note: Mapped[str | None] = mapped_column(String(256))`
   - `updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)`

2. **`BuybackAlertDelivery`（`backend/app/models/notification.py`）**：
   - 表名：`buyback_alert_deliveries`
   - 字段：
     - `id: str`（PK，UUID）
     - `user_id: str`（FK `users.id` ON DELETE CASCADE）
     - `account_id: str`（FK `accounts.id` ON DELETE CASCADE）
     - `security_id: str`（FK `securities.id` ON DELETE CASCADE）
     - `watchlist_item_id: str`（FK `watchlist_items.id` ON DELETE CASCADE）
     - `target_signature: str`（`String(128)`，如 `BUYBACK|12.340000`）
     - `trigger_date: str`（`String(10)`，北京时间 `YYYY-MM-DD`）
     - `channel_type: str`（`String(32)`）
     - `triggered_price: Decimal`（`ExactDecimal(6)`）
     - `target_price: Decimal`（`ExactDecimal(6)`）
     - `is_estimated: bool`（默认 `False`）
     - `sent_at: datetime`（UTC）
   - 索引与约束：
     - `UniqueConstraint("user_id", "watchlist_item_id", "target_signature", "trigger_date", "channel_type", name="uq_buyback_alert_deliveries_dedup")`
     - `Index("ix_buyback_alert_deliveries_user_date", "user_id", "trigger_date")`

### 2.2 Alembic 迁移

- 新增 `backend/alembic/versions/0008_watchlist_buyback_alerts.py`，`down_revision = "0007_security_daily_metrics"`。
- 使用 `batch_alter_table("watchlist_items")`（兼容 SQLite）无损添加 4 列（`alert_enabled` server_default `'0'`，`updated_at` 回填为 `created_at` 或当前 UTC 时间），并创建 `buyback_alert_deliveries` 表与索引。
- 严格遵循只增不删原则，不修改或清空 `ledger.db` 现有记录。

---

## 3. 后端服务与 API 契约

### 3.1 Schema（`backend/app/api/schemas/security.py`）

- `WatchlistItemCreate`：
  - `security_id: str`
  - `account_id: str | None = None`
  - `sort_order: int = 0`
  - `target_buy_price: Decimal | None = None`
  - `alert_enabled: bool = False`
  - `note: str | None = Field(default=None, max_length=256)`
- `WatchlistItemUpdate`（`PATCH` 请求体，使用 `model_fields_set` 区分“未传”与“显式清空为 `None`”）：
  - `target_buy_price: Decimal | None = None`
  - `alert_enabled: bool | None = None`
  - `note: str | None = Field(default=None, max_length=256)`
  - `sort_order: int | None = None`
- 校验规则：
  - 若 `target_buy_price is not None`，必须 `> 0`。
  - 若最终合并后的 `alert_enabled is True`，则 `target_buy_price` 不能为空（否则抛出 `ValidationError`）。
  - 若清空 `target_buy_price`（显式传 `null`）且未显式开启提醒，自动将 `alert_enabled` 置为 `False`。

### 3.2 聚合服务（新增 `backend/app/services/watchlist.py`）

新增 `WatchlistService`，负责将 `WatchlistItem` 列表投影为前端可直接渲染的只读视图，避免在 `router.py` 堆积聚合查询：
- `list_enriched(session, *, account_id, quote_service, amplitude_service, now)`：
  1. 查询当前账户全部 `WatchlistItem` + `Security`（按 `sort_order, created_at` 排序）。
  2. 查询这些 `security_id` 在当前账户下的：
     - 是否存在未结清批次（`Lot.status == "OPEN" and Lot.remaining_qty > 0`）→ `hasOpenPosition`
     - 默认/最近策略 ID（用于前端点击跳转详情面板）→ `strategyId`
     - 累计已实现收益（所有 `Lot` 的 `realized_pnl` 之和）→ `realizedPnl`
     - 最近一次有效卖出成交（`Trade.side == "SELL" and Trade.status == "ACTIVE"`）→ `lastSellPrice`、`lastSellExecutedAt`
  3. 从 `quote_service.read_cached` 读取统一行情快照，序列化为与 `DashboardRead.quotes` 一致的 `quote` DTO。
  4. 调用 `amplitude_service.get_or_refresh(..., allow_refresh=False)` 读取缓存的 20 日平均振幅（不阻塞关注列表请求）。
  5. 派算：
     - `vsLastSellPct = (last_price - last_sell_price) / last_sell_price`（当两者均有效且 `last_sell_price > 0`）
     - `vsTargetBuyPct = (last_price - target_buy_price) / target_buy_price`（当两者均有效且 `target_buy_price > 0`）
     - `hitBuyTarget = bool(last_price is not None and target_buy_price is not None and last_price <= target_buy_price)`

### 3.3 路由端点（`backend/app/api/v1/router.py` & `watchlist.py`）

- `GET /api/v1/watchlist?accountId=...`：返回 `WatchlistService.list_enriched(...)` 列表。
- `POST /api/v1/watchlist`：新增或幂等更新 `(account_id, security_id)` 的关注配置，触发 `_notify_quote_hub_scope(session)`，返回最新聚合项或配置项。
- `PATCH /api/v1/watchlist/{security_id}?accountId=...`：更新目标买回价、提醒开关、备注或排序（不存在返回 404），返回更新后的项。
- `DELETE /api/v1/watchlist/{security_id}?accountId=...`：取消关注，触发 `_notify_quote_hub_scope(session)`，返回 `204`。

---

## 4. 买回价提醒引擎设计

### 4.1 复用 `ProfitAlertEngine`（`backend/app/services/notifications/engine.py`）

- 不另起第二套轮询/监听协程，直接在同一 `evaluate_and_dispatch(symbols)` 周期内完成两类评估：
  1. 现有止盈评估（`_collect_dispatch_jobs` → `ProfitAlertDelivery`）
  2. 新增买回价评估（`_collect_buyback_dispatch_jobs` → `BuybackAlertDelivery`）
- **买回评估逻辑**：
  1. 根据变更的 `triggerable` 行情映射，查询满足以下条件的 `WatchlistItem`（联表 `Account`、`Security`、用户启用的 `NotificationChannel`）：
     - `WatchlistItem.alert_enabled.is_(True)`
     - `WatchlistItem.target_buy_price.is_not(None)` 且 `> 0`
     - `security_symbol in triggerable`
  2. 过滤处于失败冷却期内的 `(user_id, channel_type)`。
  3. 判定命中：`snapshot.last_price <= item.target_buy_price`（**不限制是否空仓，持仓中与空仓均触发**）。
  4. 生成目标指纹：`build_buyback_signature(item.target_buy_price)` → `f"BUYBACK|{item.target_buy_price:.6f}"`。
  5. 查询 `BuybackAlertDelivery` 检查 `(user_id, item.id, signature, trigger_date, channel_type)` 是否已发送，已发送则跳过。
  6. 聚合同一用户的命中项，调用 `build_buyback_markdown_batches(...)` 生成 Markdown 卡片并推送；发送成功后落库 `BuybackAlertDelivery`（处理并发 `IntegrityError`）。

### 4.2 企微消息格式（`backend/app/services/notifications/formatter.py`）

- 新增 `BuybackAlertItem` dataclass 与 `build_buyback_markdown_batches(...)`（遵循企微单条 4096 字节分片限制）：
  - 标题：`### 🎯 目标买回价到价提醒（共 N 项）`
  - 单项内容：标的名称（代码）、当前价（若为场外基金估算标明「盘中估算」）、目标买回价、较目标价偏离%、上次卖出价与较上卖涨跌幅（如有）、当前状态（`空仓待回接` / `持仓中`）、备注（如有）。

---

## 5. 前端设计

### 5.1 状态与 API 层

- `frontend/src/api/types.ts`：新增 `WatchlistItem`、`WatchlistItemUpsert`、`WatchlistItemUpdate` 类型；在 `DashboardPosition` 或前端列表视图模型 `PositionListRow` 中支持可选扩展字段：`isWatchlist?: boolean`、`isWatchlistOnly?: boolean`、`targetBuyPrice?: string | null`、`alertEnabled?: boolean`、`watchlistNote?: string | null`、`vsTargetBuyPct?: string | null`、`hitBuyTarget?: boolean`。
- `frontend/src/api/client.ts`：新增 `watchlist`、`addWatchlist`、`updateWatchlist`、`removeWatchlist`。
- `frontend/src/stores/preferences.ts`：
  - 新增 `showWatchlist = ref(readStoredBool('wing-trade:showWatchlist', true))`（默认 `true`），监听变化写回 `localStorage`。
- `frontend/src/stores/watchlist.ts`（新增 Pinia store）：
  - 管理当前账户的 `items: WatchlistItem[]`、`loading`、`bySecurityId` 索引。
  - 提供 `load(accountId)`、`add(payload)`、`update(securityId, payload, accountId)`、`remove(securityId, accountId)`。
  - 提供基于 `quotesStore.quotesMap` 的实时投影函数 `projectWatchlistWithQuotes(items, quotesMap)`，确保 SSE 推流时 `quote`、`vsLastSellPct`、`vsTargetBuyPct`、`hitBuyTarget` 实时更新。

### 5.2 首页混入与开关（`DashboardPage.vue` & `ActivePositionList.vue`）

- **工具栏开关**：
  - 桌面端与移动端在「显示已结清」旁新增 `<el-switch v-model="preferences.showWatchlist" active-text="显示关注" />`（移动端简写为 `关注`）。
- **列表合并计算（`DashboardPage.vue`）**：
  - `watchlistMap = watchlistStore.bySecurityId`
  - 先遍历 `dashboard.positions`：若 `watchlistMap.has(pos.securityId)`，为其叠加 `isWatchlist: true`、`targetBuyPrice`、`alertEnabled`、`watchlistNote`、`vsTargetBuyPct`、`hitBuyTarget`。
  - 当 `preferences.showWatchlist` 为 `true` 时，再遍历 `projectedWatchlist`：
    - 若该 `securityId` 已出现在 `dashboard.positions` 中（无论是持仓中，还是因开启 `includeClosed` 已加载的已结清仓位），**跳过不重复添加**。
    - 否则构造成空仓关注行（`isWatchlist: true, isWatchlistOnly: true, remainingQty: '0', remainingCost: '0', averageCost: '0', unrealizedPnl: null, lowestPriceGroup: null`，保留 `quote`、`lastSellPrice`、`vsLastSellPct`、`realizedPnl`、`avgAmplitude20d`、`targetBuyPrice`、`vsTargetBuyPct`、`hitBuyTarget`）并追加到列表中。
- **`ActivePositionList.vue` 适配**：
  - 筛选 Tab：在现有 `全部 / 低批盈利 / 低于上卖 / 浮亏` 基础上，增加 `关注` 筛选 Tab（统计 `isWatchlist === true` 的行数，方便一键只看关注的标的）。
  - 视觉展示（桌面端表格 + 移动端紧凑卡片）：
    - 名称列：已关注标的名称旁显示 `★` 标记；`isWatchlistOnly` 额外显示 `空仓关注` 标签；`hitBuyTarget` 显示高亮 `已到买回价` 标签。
    - 持仓/浮盈列：对 `isWatchlistOnly` 行，展示目标买回价与距目标价百分比（未设目标价则显示 `未设买回价`），避免大面积 `--`。
    - 排序兼容：空仓关注行在「市值 / 浮盈 / 最低批收益」排序时自动置后，在「今日涨跌 / 距上次卖出」排序时正常参与排序。
  - 点击空仓关注行：正常触发 `select`，打开右侧 `SecurityDetailPanel.vue`（注：需确认 `SecurityDetailPanel.vue` 在空仓标的且 `strategyId` 为空或默认策略时能正常加载分时图、历史成交与买入按钮）。

### 5.3 操作入口

1. **买回价设置弹窗（新增 `frontend/src/components/WatchlistSettingDialog.vue`）**：
   - 输入项：目标买回价（数值校验 `> 0`）、开启企微到价提醒开关（未填买回价时提示/禁用）、关注备注。
   - 支持保存配置与取消关注。
2. **详情面板（`SecurityDetailPanel.vue`）**：
   - 头部操作栏新增：
     - `★ 已关注 / ☆ 关注` 快捷切换按钮（未关注时点击直接 `addWatchlist`，已关注时可打开设置弹窗或取消关注）。
     - 若已关注且设置了 `targetBuyPrice`，在头部或副标题区展示 `买回价 ¥xx.xx`（命中时高亮）与提醒图标，点击打开 `WatchlistSettingDialog.vue`。
3. **清仓卖出联动（`SellMatchDrawer.vue`）**：
   - 计算当前 `lots` 总可用数量 `totalAvailableQty`；当 `preview.qty >= totalAvailableQty`（即本次卖出将清仓）且该标的当前尚未关注时，在预览摘要下方显示「清仓后继续关注」区域：
     - 复选框：`清仓后继续关注此标的`
     - 勾选后展开可选输入：`目标买回价` + `到价企微提醒` 开关。
   - 卖出 `api.sell(...)` 成功后，若勾选则自动调用 `watchlistStore.add(...)`（若关注调用失败，不回滚已记账卖出，弹警告提示用户可手动补加）。

---

## 6. 兼容性与回滚考量

- **向后兼容**：旧版本客户端仅调用 `POST /api/v1/watchlist`（仅传 `securityId`）仍完全可用；新字段均有合理默认值。
- **降级回滚**：若买回价推送出现异常，可随时关闭单标的 `alertEnabled` 或在「推送设置」关闭企微渠道，完全不影响主账务与行情功能。
- **数据安全**：迁移脚本仅执行 `ADD COLUMN` 与 `CREATE TABLE`，绝不触碰或清理 `ledger.db` 中已有账务数据。
