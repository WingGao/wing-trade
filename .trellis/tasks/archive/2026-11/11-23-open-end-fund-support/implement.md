# 实施计划：支持场外开放式基金简单买卖与已公布净值

## 1. 执行清单（按序）

### 阶段 A：后端市场与净值行情能力
- [x] A1. `backend/app/domain/markets.py`：新增 `OF -> CNY` 与 `is_open_fund_market()`。
- [x] A2. `backend/app/market_data/calendar.py` & `service.py`：新增 `OpenFundCalendar`，在 `QuoteService` 中为 `OF` 市场绑定基金日历。
- [x] A3. `backend/app/market_data/provider.py`、`factory.py`、`__init__.py`：新增 `EastmoneyFundQuoteProvider` 与 `CompositeQuoteProvider`，组合接入 `create_quote_provider`。
- [x] A4. `backend/app/market_data/security_info.py`：扩展 `search()` 识别场外基金，新增 `fund_nav_history()` 并在 `intraday()` / `IntradayService` 中路由与缓存。
- [x] A5. `backend/app/api/v1/market.py`、`router.py`、`services/dashboard.py`、`api/schemas/dashboard.py`、`api/schemas/trade.py`：补齐 `securityType` 与 `seriesKind` 字段，规范 `OF` 建档类型。

### 阶段 B：前端精度、单位、录入与净值图适配
- [x] B1. `frontend/src/api/types.ts`、`utils/format.ts`：补充类型与 `isFundSecurity` / `quantityUnit` / `priceDigits` / 小数份额格式化。
- [x] B2. `frontend/src/components/IntradayChart.vue`、`QuoteBadge.vue`：适配近期净值曲线（`MM-DD` + 4 位净值）与“净值”状态标签。
- [x] B3. `frontend/src/components/BuyTradeDialog.vue`、`SellMatchDrawer.vue`、`LotTable.vue`、`SettledTradeTable.vue`、`ProfitTargetDialog.vue`：适配小数份额输入、4 位净值与“份”单位。
- [x] B4. `frontend/src/pages/DashboardPage.vue`、`SecurityDetailPage.vue`、`TradesPage.vue`：支持手动新增 `OF` 基金、透传 `securityType`、禁用基金详情页 3 秒轮询、统一精度与文案。

### 阶段 C：测试补齐与 Trellis 质量门禁（`task.py start` 后执行）
- [x] C1. 新增/补充后端单元测试（注入 transport 与 clock，不访问公网）：
  - `EastmoneyFundQuoteProvider`：正常解析最新净值与前一日净值、缺失涨跌幅自动计算、无效行跳过、响应超限、部分失败与全失败、`OF` 代码校验。
  - `CompositeQuoteProvider`：A 股 + 场外基金混合路由、单源失败保留另一源成功结果。
  - `OpenFundCalendar` & `QuoteService`：`OF` 市场缓存新鲜度不按股票盘中分钟级误判过期。
  - `SecurityInfoProvider`：`search()` 区分场内 ETF 与场外基金、`OF:` 前缀过滤、`fund_nav_history()` 按日期升序输出 `series_kind="nav"`。
- [x] C2. 新增/补充后端集成测试：
  - 创建 `OF` 基金 -> 小数份额与 4 位净值买入/卖出配对 -> `Dashboard` 估值与 `/intraday` 近期净值接口端到端校验。
- [x] C3. 运行 `trellis-check` 全量检查：
  - 后端：`ruff check app tests`、`mypy app`、`pytest -v`（遵循 `.trellis/spec/backend/quality-guidelines.md` 中的 `uv run` 参数约定）。
  - 前端：`pnpm run type-check`、`pnpm run build`。
- [x] C4. 按 Phase 3.3 检查并同步更新 `.trellis/spec/backend/market-data-guidelines.md`（记录场外基金 `OF` 市场、已公布净值源与 `seriesKind="nav"` 契约）。
- [ ] C5. 按 Phase 3.4 给出分批提交计划，等待用户确认后提交，再提醒运行 `/finish-work`。

## 2. 验证命令

```powershell
# 后端 lint & type-check（沙箱内可运行）
uv run --cache-dir .uv-cache --offline --no-sync --project backend ruff check backend/app backend/tests
uv run --cache-dir .uv-cache --offline --no-sync --project backend mypy backend/app

# 后端全量测试（Windows 异步/集成测试需非沙箱）
uv run --cache-dir .uv-cache --offline --no-sync --project backend pytest backend/tests -v

# 前端类型检查与构建
pnpm --dir frontend run type-check
pnpm --dir frontend run build
```

## 3. 审阅门禁与回滚点

- **Phase 1 门禁**：用户审阅并确认 `prd.md`、`design.md`、`implement.md` 后，执行 `python ./.trellis/scripts/task.py start 11-23-open-end-fund-support` 进入 Phase 2。
- **Phase 2 门禁**：全部新增单测、集成测试、lint、type-check、前端 build 通过，无跨层不一致。
- **回滚点**：所有改动不涉及数据库 schema 变更，任一阶段均可按 git diff 安全回退。
