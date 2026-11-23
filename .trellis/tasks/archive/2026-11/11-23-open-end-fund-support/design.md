# 技术设计：支持场外开放式基金简单买卖与已公布净值

## 1. 设计目标与原则

- **复用优先**：充分利用现有 `Security.security_type`、`ExactDecimal(4)` 份额精度、`ExactDecimal(6)` 价格精度与 `QuoteCache` 共享缓存表，避免不必要的表结构变更。
- **边界清晰**：将场外基金（`OF`）的行情拉取、新鲜度日历与历史净值曲线封装在独立适配类中，不破坏 `EastmoneyQuoteProvider` 原有“仅支持 SH/SZ/BJ”的单元测试与规范契约。
- **派生隔离**：净值行情与近期曲线仍为派生展示数据，上游超时或格式异常时保留旧缓存并标记 `is_stale=True`，绝不改写账务事实或用 `0` 替代缺失净值。

## 2. 领域与数据模型边界

### 2.1 市场与币种（`backend/app/domain/markets.py`）
- 在 `MARKET_CURRENCY` 中新增 `"OF": "CNY"`，表示中国场外开放式公募基金。
- 新增 `is_open_fund_market(market: str | None) -> bool`，供各层复用判断。
- `POST /api/v1/securities` 在 `market == "OF"` 且调用方未显式指定类型（仍为默认 `"STOCK"`）时，自动规范为 `security_type="FUND"`。

### 2.2 既有数据库表兼容性
- `securities.market` 为 `String(8)`、`securities.security_type` 为 `String(32)`，直接支持 `"OF"` 与 `"FUND"`。
- `trades.qty` / `lots.*_qty` 为 `ExactDecimal(4)`，`trades.price` / `quote_cache.last_price` 为 `ExactDecimal(6)`，完全满足 2~4 位小数份额与 4 位单位净值精度，**无需 Alembic 迁移**。

## 3. 行情源、日历与路由设计

### 3.1 场外基金日历（`backend/app/market_data/calendar.py`）
- 新增 `OpenFundCalendar(AShareTradingCalendar)`：
  - `is_session_open()` 恒返回 `False`（场外基金无盘中连续竞价）。
  - 缓存 TTL 默认 `6 * 3600` 秒（6 小时），避免盘中按股票 10 秒规则误判过期，同时晚间净值更新后能及时拉新。
- `QuoteService.calendar_for_security(security)` 对 `market == "OF"` 返回 `OpenFundCalendar`。

### 3.2 基金已公布净值行情源（`backend/app/market_data/provider.py`）
- 新增 `EastmoneyFundQuoteProvider`：
  - `name = "eastmoney-fund"`，`supported_markets = frozenset({"OF"})`。
  - `supports_symbol(symbol)` 校验 `OF:` + 6 位数字代码。
  - 上游接口：`https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=2`（附带 `Referer: https://fundf10.eastmoney.com/`）。
  - 数值解析：限制响应体上限（2MB），使用 `json.loads(..., parse_float=Decimal, parse_constant=_reject_json_constant)`，跳过缺失或 `<= 0` 的 `DWJZ`；取最新有效行 `DWJZ` 为 `last_price`，次新有效行 `DWJZ` 为 `prev_close`，`JZZZL` 为 `change_pct`（缺失时由 `QuoteSnapshot` 自动精确计算）；`FSRQ` 按上海时区 `15:00` 转 UTC 作为 `source_time`。
  - 容错：多只基金逐只拉取，单只失败不阻塞其他基金；仅当全部失败且无有效结果时抛出异常。
- 新增 `CompositeQuoteProvider`：
  - 组合多个 `QuoteProvider`，按子源 `supports_symbol` 分组请求并合并结果；部分子源失败不丢弃其他子源成功结果。
  - `create_quote_provider()` 在 `QUOTE_PROVIDER=eastmoney` 时返回 `CompositeQuoteProvider([EastmoneyQuoteProvider(...), EastmoneyFundQuoteProvider(...)], name="eastmoney")`。

### 3.3 查名与近期历史净值曲线（`backend/app/market_data/security_info.py`）
- `SecurityCandidate` 增加 `security_type: str = "STOCK"`。
- `SecurityInfoProvider.search(keyword)`：
  - 支持 `OF:` 前缀过滤。
  - 保持现有判断顺序：**先识别 `MktNum in {"0", "1"}` 的沪深北场内证券（含场内股票与场内 ETF）和美股**，再将剩余 6 位数字且 `Classify in {"FUND", "OPENFUND"}` 或 `SecurityTypeName` 含 `"基金"` 或 `QuoteID` 以 `"7."` 开头的记录识别为 `SecurityCandidate("OF", code, name, "FUND")`。
- `IntradaySnapshot` 增加 `series_kind: Literal["intraday", "nav"] = "intraday"`。
- 新增 `SecurityInfoProvider.fund_nav_history(symbol, *, days=30)`：
  - 调用 `https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=30`。
  - 过滤有效正数 `DWJZ`，按 `FSRQ` 升序组装 `points`，并将最新一条组装为 `QuoteSnapshot` 返回（`series_kind="nav"`，`trading_date` 为最新净值日期）。
- `SecurityInfoProvider.intraday(symbol)` 遇到 `OF:` 自动委托给 `fund_nav_history`。
- `IntradayService.read(symbol)` 对 `OF:` 使用 5 分钟防抖缓存，且不因跨自然日而清空历史净值曲线。

## 4. API 契约变化（向后兼容）

- `GET /api/v1/securities/lookup`：候选对象新增 `securityType`（`"STOCK"` / `"FUND"`）。
- `GET /api/v1/securities/{security_id}/intraday`：响应新增 `seriesKind`（`"intraday"` / `"nav"`）。
- `GET /api/v1/dashboard`（`DashboardPositionRead`）与 `GET /api/v1/trades`（`TradeRead`）：新增可选字段 `securityType`，与既有字段完全兼容。

## 5. 前端适配设计

1. **统一判断与格式化（`frontend/src/utils/format.ts`）**
   - `isFundSecurity(target)`：基于 `market === 'OF' || securityType === 'FUND' || symbol.startsWith('OF:')` 判定。
   - `formatQuantity(value, isFund)`：非基金维持整数（0 位），基金按 2~4 位小数展示。
   - `quantityUnit(target)`：基金返回 `"份"`，非基金返回 `"股"`。
   - `priceDigits(target, defaultDigits)`：基金固定 4 位，其余沿用原默认精度。
2. **图表与详情页（`IntradayChart.vue`、`SecurityDetailPage.vue`）**
   - `seriesKind === 'nav'` 时，横轴显示 `MM-DD`，纵轴显示 4 位净值，标题显示“近期单位净值走势 · 已公布净值”。
   - 场外基金详情页禁用 3 秒定时轮询（`schedulePoll()` 直接返回），避免无意义请求。
3. **行情徽章与表单/列表组件**
   - `QuoteBadge.vue`：场外基金有效状态显示“净值”而非“实时”，净值保留 4 位小数。
   - `DashboardPage.vue`：手动新增证券支持选择“场外基金（`OF`）”，选中外部候选时透传 `securityType`。
   - `BuyTradeDialog.vue`、`SellMatchDrawer.vue`、`ProfitTargetDialog.vue`、`LotTable.vue`、`SettledTradeTable.vue`、`TradesPage.vue`：基金模式下开放小数输入（`inputmode="decimal"`），展示小数份额、4 位净值及“份”单位。

## 6. 兼容性与回滚策略

- **向后兼容**：所有新增 API 字段均向后兼容，不影响旧客户端与既有 `SH`/`SZ`/`BJ`/`US` 标的。
- **回滚**：无数据库迁移，如需回滚仅需撤销对应代码提交，对既有 `ledger.db` 无副作用。
