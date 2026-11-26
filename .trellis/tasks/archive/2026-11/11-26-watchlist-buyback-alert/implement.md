# 清仓关注与目标买回价提醒 — 实施计划（Implement）

## 1. 有序实施清单

### 阶段 A：数据库模型与无损迁移
- [ ] A1. 修改 `backend/app/models/ledger.py`：为 `WatchlistItem` 增加 `target_buy_price`、`alert_enabled`、`note`、`updated_at` 字段及完整中文注释。
- [ ] A2. 修改 `backend/app/models/notification.py` 与 `backend/app/models/__init__.py`：新增 `BuybackAlertDelivery` 模型及导出。
- [ ] A3. 新增 `backend/alembic/versions/0008_watchlist_buyback_alerts.py`（`down_revision = "0007_security_daily_metrics"`），支持 SQLite 无损 `batch_alter_table` 增列与建表。

### 阶段 B：后端关注服务与分离 API
- [ ] B1. 修改 `backend/app/api/schemas/security.py` 与 `backend/app/api/schemas/__init__.py`：扩展 `WatchlistItemCreate`、`WatchlistItemRead`，新增 `WatchlistItemUpdate` 及校验逻辑。
- [ ] B2. 新增 `backend/app/services/watchlist.py`：实现 `WatchlistService`（增改删与 `list_enriched` 聚合投影，计算 `hasOpenPosition`、`lastSellPrice`、`vsLastSellPct`、`vsTargetBuyPct`、`hitBuyTarget`、`realizedPnl`、`avgAmplitude20d` 与 `quote`）。
- [ ] B3. 修改 `backend/app/api/v1/router.py` 与 `backend/app/api/v1/watchlist.py`：升级 `GET/POST/DELETE /api/v1/watchlist`，新增 `PATCH /api/v1/watchlist/{security_id}`。

### 阶段 C：目标买回价提醒引擎（复用企微推送）
- [ ] C1. 修改 `backend/app/services/notifications/formatter.py`：新增 `BuybackAlertItem` 与 `build_buyback_markdown_batches`。
- [ ] C2. 修改 `backend/app/services/notifications/engine.py`：新增 `build_buyback_signature` 与 `_collect_buyback_dispatch_jobs`，在 `evaluate_and_dispatch` 中同步评估并推送买回提醒（空仓与持仓均触发），落库 `BuybackAlertDelivery` 按自然日 + 目标价指纹去重。

### 阶段 D：后端测试
- [ ] D1. 新增集成测试 `backend/tests/integration/test_watchlist_api.py`：覆盖增删改查、参数校验、用户/账户隔离、聚合字段计算、重买保留与清仓后延续。
- [ ] D2. 新增集成测试 `backend/tests/integration/test_buyback_alert_notifications.py`：覆盖空仓触发、持仓中触发、自然日去重、改目标价后重触发、闭市/过期行情不误报。

### 阶段 E：前端状态、混入展示与交互入口
- [ ] E1. 修改 `frontend/src/api/types.ts`、`frontend/src/api/client.ts`：补齐 Watchlist 类型与 API 方法。
- [ ] E2. 修改 `frontend/src/stores/preferences.ts`：新增 `showWatchlist` 开关（默认 `true`，持久化 `localStorage`）。
- [ ] E3. 新增 `frontend/src/stores/watchlist.ts`：管理关注数据、CRUD 与 SSE 行情实时投影（`projectWatchlistWithQuotes`）。
- [ ] E4. 新增 `frontend/src/components/WatchlistSettingDialog.vue`：目标买回价、企微提醒开关、备注配置弹窗。
- [ ] E5. 修改 `frontend/src/pages/DashboardPage.vue` 与 `frontend/src/components/ActivePositionList.vue`：
  - 桌面端与移动端新增「显示关注」开关。
  - 按开关前端混入空仓关注行，持仓中已关注行叠加星标，同时开启 `includeClosed` 时按 `securityId` 去重，确保汇总卡片不受影响。
  - 增加「关注」筛选 Tab 与空仓关注行专属展示（目标买回价、距目标价%、到价高亮）。
- [ ] E6. 修改 `frontend/src/components/SecurityDetailPanel.vue`：头部增加关注切换与买回价设置入口，确认空仓关注标的打开详情面板正常工作。
- [ ] E7. 修改 `frontend/src/components/SellMatchDrawer.vue`：识别清仓卖出且未关注时展示「清仓后继续关注」与可选目标买回价输入，卖出成功后自动提交关注。

### 阶段 F：前端测试与全量校验
- [ ] F1. 补充/运行前端工具函数与组件相关测试（验证混入去重、SSE 投影、`showWatchlist` 持久化逻辑）。
- [ ] F2. 全量验证命令通过，对照 `prd.md` 逐项核对验收标准。

---

## 2. 验证命令

```bash
# 后端编译与测试
uv run --cache-dir .uv-cache --project backend python -m compileall -q backend/app backend/tests
uv run --cache-dir .uv-cache --project backend pytest backend/tests/integration/test_watchlist_api.py backend/tests/integration/test_buyback_alert_notifications.py
uv run --cache-dir .uv-cache --project backend pytest

# 后端静态检查（如已安装 ruff/mypy）
uv run --cache-dir .uv-cache --project backend ruff check backend/app backend/tests

# 前端类型检查、单测与构建
pnpm --dir frontend run type-check
pnpm --dir frontend run build
```

## 3. 风险点与回滚策略

- **本地 `ledger.db` 安全**：测试全部使用内存或临时 SQLite 数据库，绝不指向或清空根目录 `ledger.db`；仅在验证启动迁移时对 `ledger.db` 执行只读或安全 `upgrade head`。
- **空仓标的打开详情面板**：需检查 `SecurityDetailPanel.vue` 对 `remainingQty == 0`、`strategyId` 缺失时的边界处理，防止空指针或错误调用刷新重算。
- **SSE 订阅覆盖**：`POST / DELETE /api/v1/watchlist` 必须保持调用 `_notify_quote_hub_scope(session)`，确保刚加入关注的清仓股立即收到实时推送。
- **回滚点**：每个阶段（A 迁移 / B 接口 / C 提醒 / E 前端）边界清晰，若某阶段出现回归可直接按阶段回退。
