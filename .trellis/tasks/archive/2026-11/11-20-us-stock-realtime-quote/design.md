# 支持美股和实时行情 — 技术设计

## 1. 设计目标与架构原则

1. **零数据库破坏性变更**：复用现有 `accounts`、`securities`、`quote_cache` 表结构（`Trade.qty` 等已是 `ExactDecimal(4)`），无需新增数据库迁移脚本，保证本地 `ledger.db` 100% 兼容且无损。
2. **行情派生与账务隔离**：延续 `market-data-guidelines.md` 契约，行情请求失败或限流绝不影响买卖、编辑、撤销与账务查询。
3. **组合路由与独立容错**：美股行情作为独立 `EastmoneyUSQuoteProvider` 接入 `CompositeQuoteProvider`，与 A 股、场外基金完全解耦。
4. **全链路离线可测**：所有新增行情源、市场码解析器、日历均支持注入 `transport` 与 `now` 时钟，自动化测试不访问公网。

---

## 2. 后端技术设计

### 2.1 美股扩展交易日历（`backend/app/market_data/calendar.py`）

扩展 `USTradingCalendar`，新增时段识别方法：

```python
MarketSessionPhase = Literal["pre", "regular", "post", "closed"]
```

- 时段边界（美东时间 `America/New_York`，工作日且非节假日）：
  - `pre`：`04:00 <= t < 09:30`
  - `regular`：`09:30 <= t <= 16:00`
  - `post`：`16:00 < t <= 20:00`
  - `closed`：其余时间及周末/节假日
- `USTradingCalendar.session_phase(value: datetime | None = None) -> MarketSessionPhase`：返回当前所处的美股时段。
- `USTradingCalendar.is_session_open(value: datetime | None = None) -> bool`：当 `session_phase(value) != "closed"` 时返回 `True`（盘前、盘中、盘后均视为活跃时段，TTL 10 秒，与 A 股一致；休市 TTL 3600 秒）。
- `AShareTradingCalendar.session_phase(value: datetime | None = None) -> MarketSessionPhase`：提供统一基类接口（盘中返回 `"regular"`，否则 `"closed"`），`OpenFundCalendar.session_phase()` 恒返回 `"closed"`。

### 2.2 共享美股交易所码解析器（`backend/app/market_data/us_market.py` 或合入 `provider.py`）

新增轻量、线程安全的 `USMarketResolver`：
- **职责**：将美股代码（如 `AAPL`、`BABA`、`SPY`）映射为东方财富市场编号（`"105"` / `"106"` / `"107"`）。
- **解析链路**：
  1. 命中内存 LRU 缓存（容量 512）直接返回。
  2. 调用东方财富 `searchapi.eastmoney.com/api/suggest/get` 精确匹配 `Code.upper() == symbol` 且 `MktNum in {"105", "106", "107"}`。
  3. 写入缓存并返回；查无结果返回 `None`。
- **共享复用**：`EastmoneyUSQuoteProvider` 与 `SecurityInfoProvider` 共享同一解析能力；`SecurityInfoProvider.search()` 查到的美股市场码同步写入缓存，避免重复请求。

### 2.3 美股批量行情源（`backend/app/market_data/provider.py` & `factory.py`）

1. **`QuoteSnapshot` 扩展**：
   - 增加可选字段 `market_session: MarketSessionPhase | None = None`（默认值 `None`，向后兼容所有既有测试与调用方）。
   - `stale()` 保留 `market_session` 字段。

2. **新增 `EastmoneyUSQuoteProvider`**：
   - `name = "eastmoney-us"`
   - `supported_markets = frozenset({"US"})`
   - `supports_symbol(symbol)`：匹配 `US:` 前缀且代码符合美股 ticker 规范（1~12 位字母/数字/点/短横线，如 `AAPL`、`BRK.B`、`BF-B`）。
   - 请求接口：`https://push2.eastmoney.com/api/qt/ulist.np/get`
     - `fltt=2&invt=2&secids=...&fields=f12,f13,f14,f2,f3,f18,f124,f251,f253,f292`
     - 单批最多 100 只，响应体上限 2MB，严格 `Decimal` 解析（拒绝 `NaN`/`Infinity`）。
   - **盘前/盘后价格选取规则**：
     - 结合当前时钟 `USTradingCalendar.session_phase(fetched_at)`：
       - 若处于 `"pre"` 或 `"post"` 且上游返回有效正数 `f251`（扩展时段最新价）：
         - `last_price = f251`
         - `change_pct = f253`（若 `f253` 有效；否则按 `prev_close` 或常规价计算）
         - `market_session = phase`（`"pre"` 或 `"post"`）
       - 否则回退常规价 `f2`、`f3`、`f18`，`market_session` 标记为当前 `session_phase(source_time or fetched_at)`（如 `"regular"` 或 `"closed"`）。
   - **未缓存市场码的兜底探测**：若 `USMarketResolver` 未命中，且证券数较少，允许在单次批量中尝试 `105.{code},106.{code},107.{code}` 并以返回有效价格的行回填缓存。
   - **接入组合源**：`create_quote_provider()` 在 `eastmoney` 模式下注册：
     ```python
     CompositeQuoteProvider([
         EastmoneyQuoteProvider(...),
         EastmoneyUSQuoteProvider(...),
         EastmoneyFundQuoteProvider(...),
     ], name="eastmoney")
     ```

### 2.4 美股缓存与时段推导（`backend/app/market_data/service.py`）

- 不修改 `quote_cache` 表结构。
- `QuoteService.read_cached()` 读取 `US` 证券缓存时，根据 `source_time or fetched_at` 通过 `us_calendar.session_phase(...)` 推导 `market_session`（若快照未显式指定）；对于 `SH/SZ/BJ` 在开盘时段推导为 `"regular"`，休市为 `"closed"`；`OF` 保持 `None`。

### 2.5 Dashboard 多市场日历感知（`backend/app/services/dashboard.py` & `schemas/dashboard.py`）

- 新增辅助方法 `DashboardService._calendar_for_account(account: Account) -> AShareTradingCalendar`：
  - `account.currency == "USD"` → 返回 `self.quote_service.us_calendar`
  - 其他（`CNY`）→ 返回 `self.calendar`（A 股日历）
- `DashboardService.build()` 使用 `account_calendar = self._calendar_for_account(account)` 计算：
  - `session_open = account_calendar.is_session_open(current)`
  - `market_session = account_calendar.session_phase(current)`
  - `poll_after_seconds = 10 if session_open else 60`
- DTO 扩展（向后兼容，均设为可选或带默认值）：
  - `DashboardRead` 新增 `market_session: Literal["pre", "regular", "post", "closed"] = "closed"`。
  - `QuoteRead` 新增 `market_session: Literal["pre", "regular", "post", "closed"] | None = None`。

### 2.6 美股分时曲线修复（`backend/app/market_data/security_info.py` & `api/v1/market.py`）

- `SecurityInfoProvider.intraday()` 针对 `US` 证券：
  - 复用 `USMarketResolver` 获取 `upstream_market`（`105`/`106`/`107`）。
  - 请求 `trends2/get` 时，对美股传 `iscr=1`（包含盘前/盘后分钟点），A 股保持 `iscr=0`。
  - **时区与交易日归属修正**：
    - 东方财富 `trends2/get` 返回的时间字符串为北京时间（`Asia/Shanghai`）。解析为 `Asia/Shanghai` 后转为 `UTC`，再用 `calendar.timezone`（`America/New_York`）提取日期与 `today`（美东当前日期）比对。
    - 若当前为美东早间盘前之前（如美东 `00:00–04:00`），按 `today` 过滤不会误将上一交易日数据当作今日。
  - `QuoteSnapshot` 附上 `market_session=self.us_calendar.session_phase(points[-1].time)`。
  - `/api/v1/securities/{security_id}/intraday` 响应中 `quote` 透传 `marketSession`。

---

## 3. 前端技术设计

### 3.1 全局账户状态管理（新增 `frontend/src/stores/accounts.ts`）

- 状态：
  - `accounts: Ref<Account[]>`
  - `activeAccountId: Ref<string>`（持久化到 `localStorage`，键名含用户 ID 隔离，如 `wing_trade_active_account:<userId>`，登出时清理）
  - `activeAccount: ComputedRef<Account | null>`
- 方法：
  - `loadAccounts(force = false)`：拉取 `/api/v1/accounts`，若当前 `activeAccountId` 无效则优先选第一个 CNY 账户或列表首项。
  - `setActiveAccount(accountId: string)`：切换全局账户并通知各页面响应。
  - `createAccount(payload: { name: string; currency: Currency; broker?: string })`：调用 `api.createAccount`，刷新列表并自动切换到新建账户。

### 3.2 全局账户切换器与新建账户弹窗（`frontend/src/App.vue` + `frontend/src/components/CreateAccountDialog.vue`）

- 侧边栏品牌文案从 `A 股交易账本` 调整为 `翼·交易账本`。
- 桌面端顶栏与移动端顶栏（紧凑下拉）增加全局账户切换器，展示 `账户名 (USD/CNY)` 标签，并提供 `+ 新建账户` 入口。
- 新建账户弹窗支持输入：账户名称（必填）、币种（`CNY 人民币` / `USD 美元`）、券商名称（选填）。

### 3.3 多页面账户联动与多币种展示

1. **总览页（`DashboardPage.vue`）**：
   - 监听 `accountsStore.activeAccountId`，切换账户时自动取消旧轮询并调用 `ledger.loadDashboard({ accountId, ... })`。
   - 快速记账中若用户选中美股（`USD`）且账户列表里尚无 `USD` 账户，显示“当前无美元账户，点击一键新建”按钮，打开 `CreateAccountDialog`（预选 `USD`）。
   - 总览头部状态条对美股显示当前时段提示（如“美股盘前”、“美股盘中”、“美股盘后”、“已休市”）。
2. **交易流水页（`TradesPage.vue`）**：
   - `api.trades(accountId?)` 支持透传 `accountId` 查询参数。
   - 顶部筛选增加账户选择器（默认跟随全局账户，额外提供“全部账户”选项）。
   - 价格与费用列使用 `formatMoney(..., row.currency, ...)` 显示对应币种符号；数量列按证券市场格式化（支持美股碎股）。
3. **复盘分析页（`AnalyticsPage.vue`）**：
   - `api.summary(accountId?)` 与 `api.cycles(securityId?, accountId?)` 透传当前 `activeAccountId`。
   - 监听 `activeAccountId` 变化自动重拉汇总与周期。
   - 所有金额指标改用 `formatMoney(..., summary?.currency)`，彻底移除硬编码 `¥`。
   - 并行加载本地证券列表，周期表展示 `MARKET:SYMBOL 名称` 替代原始 `securityId`。

### 3.4 美股碎股、手续费与盘前/盘后标识

1. **格式化工具（`frontend/src/utils/format.ts`）**：
   - 新增 `isUSSecurity(target)` 辅助函数。
   - 更新 `formatQuantity(value, target)`：
     - 兼容原有 `boolean`（`isFund`）传参，同时支持传入证券对象 `{ market, securityType, symbol }`。
     - 场外基金：2~4 位小数（`getNumberFormatter(2, 4)`）。
     - 美股（或含非零小数的股数）：0~4 位小数（`getNumberFormatter(0, 4)`），整数股显示整数（如 `10`），碎股完整保留小数（如 `0.25`、`1.3333`）。
     - A 股整数：保持整数展示。
2. **报价徽章（`frontend/src/components/QuoteBadge.vue`）**：
   - 当行情未过期且 `quote.marketSession === 'pre'` 时，标签显示 `盘前`（浅蓝/warning 区分样式）；
   - `quote.marketSession === 'post'` 时，标签显示 `盘后`；
   - `regular` / 默认股票显示 `实时`，场外基金显示 `净值`。
   - Tooltip 中补充展示“交易时段：盘前 / 盘中 / 盘后”。
3. **买入与卖出弹窗（`BuyTradeDialog.vue` & `SellMatchDrawer.vue`）**：
   - 新增 `isUS`（或 `market`）属性感知：
     - 数量输入框在美股或基金时启用 `inputmode="decimal"`，美股 placeholder 提示“支持碎股小数，如 0.5”。
     - 买入弹窗新增“其他/平台费”输入项（映射到 `otherFee`），美股场景标签提示“佣金”、“平台/监管费”。
     - 卖出抽屉新增“佣金”与“其他/平台费”输入项，纳入 `previewKey` 计算并透传给 `api.previewSell` 和 `api.sell`。

---

## 4. 兼容性、风险与测试策略

- **向后兼容**：所有新增 API 字段（`marketSession`）均为可选或带默认值，旧前端/旧测试不受影响。
- **反爬与限流**：美股批量行情复用现有 `QuoteCollector` 串行锁、最小刷新间隔（10s）与连续 3 次失败指数退避熔断机制；市场码解析结果常驻 LRU 缓存，避免高频调用 `suggest`。
- **测试矩阵**：
  - 后端单测（新增 `backend/tests/unit/test_us_market_data.py`）：`USTradingCalendar` 四时段与夏/冬令时、`USMarketResolver`、`EastmoneyUSQuoteProvider`（常规/盘前/盘后/缺字段回退/无效行/超限/多批部分失败）、`CompositeQuoteProvider` 三市场混合路由、`SecurityInfoProvider.intraday` 美股 `iscr=1` 与跨北京时间午夜归属。
  - 后端集成测试（新增 `backend/tests/integration/test_us_stock_api.py`）：创建 USD 账户、建档美股、碎股小数买入+含费卖出配对、`/api/v1/dashboard` 美股时段与估值、`/api/v1/trades?accountId=`、`/api/v1/analytics/summary?accountId=`、`/api/v1/securities/{id}/intraday`。
  - 前端校验：`pnpm type-check`、`pnpm build`（及必要单测/格式化测试）。
