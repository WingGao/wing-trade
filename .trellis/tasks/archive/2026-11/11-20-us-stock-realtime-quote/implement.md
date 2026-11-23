# 支持美股和实时行情 — 实施计划

## 阶段一：后端美股交易日历、市场码解析与批量行情源

1. **扩展交易日历**（`backend/app/market_data/calendar.py`）
   - 为 `AShareTradingCalendar`、`OpenFundCalendar` 增加 `session_phase()` 统一接口。
   - 扩展 `USTradingCalendar` 支持 `pre`（04:00–09:30 ET）、`regular`（09:30–16:00 ET）、`post`（16:00–20:00 ET）、`closed` 四时段，并在活跃时段（含盘前/盘后）使 `is_session_open()` 返回 `True`。
2. **扩展 `QuoteSnapshot` 与新增美股批量行情源**（`backend/app/market_data/provider.py`、`factory.py`、`__init__.py`）
   - `QuoteSnapshot` 增加可选 `market_session` 字段。
   - 实现 `USMarketResolver`（LRU 缓存 + 东方财富 `suggest` 精确匹配 `105/106/107`）。
   - 实现 `EastmoneyUSQuoteProvider`（支持 `US:*` 批量 `ulist.np/get`、盘前/盘后 `f251`/`f253` 优先与常规价平滑回退、Decimal 精确解析、响应大小限制）。
   - 在 `create_quote_provider()` 的 `CompositeQuoteProvider` 中注册 `EastmoneyUSQuoteProvider`。
3. **修复美股分时与缓存时段推导**（`backend/app/market_data/security_info.py`、`service.py`、`api/v1/market.py`）
   - `SecurityInfoProvider` 接入共享 `USMarketResolver`，美股分时请求传 `iscr=1`。
   - 修复美股分时时间戳按上海时区解析转 UTC 再按美东交易日归属的逻辑。
   - `QuoteService.read_cached()` 推导 `market_session`；`/api/v1/securities/{id}/intraday` 透传 `marketSession`。
4. **Dashboard 多市场日历感知**（`backend/app/services/dashboard.py`、`api/schemas/dashboard.py`）
   - `DashboardService.build()` 按账户币种选择 A 股或美股日历，输出 `marketSessionOpen`、`marketSession`、`pollAfterSeconds` 及行情 `marketSession`。

## 阶段二：后端自动化测试验证

5. 新增单元测试 `backend/tests/unit/test_us_market_data.py` 与集成测试 `backend/tests/integration/test_us_stock_api.py`。
6. 运行后端全量测试与静态检查：
   ```powershell
   uv run --project backend pytest backend/tests
   ```

## 阶段三：前端全局账户管理、多币种与美股交互适配

7. **API 与类型扩展**（`frontend/src/api/types.ts`、`frontend/src/api/client.ts`）
   - 补充 `MarketSessionPhase`、`Quote.marketSession`、`DashboardRead.marketSession`。
   - `api.trades(accountId?)`、`api.summary(accountId?)`、`api.cycles(securityId?, accountId?)` 支持透传 `accountId`。
8. **全局账户 Store 与新建账户弹窗**（新增 `frontend/src/stores/accounts.ts`、`frontend/src/components/CreateAccountDialog.vue`；修改 `frontend/src/App.vue`）
   - 实现全局账户加载、切换（按用户持久化）与新建（CNY/USD）。
   - 桌面端与移动端顶栏接入账户切换器与新建按钮；侧边栏标题去“A 股”硬编码。
9. **格式化工具与盘前/盘后徽章**（`frontend/src/utils/format.ts`、`frontend/src/components/QuoteBadge.vue`）
   - `formatQuantity` 适配美股碎股（最多 4 位有效小数，整数不补零）。
   - `QuoteBadge` 渲染 `盘前` / `实时` / `盘后` / `净值` 标签及 Tooltip 时段说明。
10. **页面与交易表单适配**
    - `DashboardPage.vue`：联动全局账户、展示美股时段提示、快速记账缺 USD 账户时一键新建引导。
    - `TradesPage.vue`：支持账户筛选（含“全部账户”）、币种格式化与碎股数量展示。
    - `AnalyticsPage.vue`：联动全局账户、移除硬编码 `¥`、周期表展示证券代码与名称。
    - `SecurityDetailPage.vue`、`LotTable.vue`、`SettledTradeTable.vue`：透传美股市场标识以正确展示碎股与盘前/盘后标签。
    - `BuyTradeDialog.vue`、`SellMatchDrawer.vue`：支持美股小数数量输入、补齐佣金与其他/平台费用录入（含卖出预览与提交）。

## 阶段四：全栈质量检查与规范同步

11. 前端类型检查与构建验证：
    ```powershell
    pnpm --dir frontend type-check
    pnpm --dir frontend build
    ```
12. 后端全量测试复核，对照 `.trellis/spec/` 检查所有改动并更新 `market-data-guidelines.md`（补充美股批量行情、盘前盘后与扩展日历契约）。
13. 整理提交计划并交由用户确认。
