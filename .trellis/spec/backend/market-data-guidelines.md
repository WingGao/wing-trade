# Market Data Guidelines

## Scenario: Shared quote cache and `/api/v1/quotes`

### 1. Scope / Trigger

This contract applies to quote providers, cache persistence, refresh orchestration,
and target reminder evaluation. Quote data is derived display data and must never
block or rewrite ledger facts.

### 2. Signatures

- `QuoteProvider.get_quotes(symbols: Sequence[str]) -> dict[str, QuoteSnapshot]`
- `QuoteProvider.health() -> ProviderHealth`
- `QuoteCollector.collect(session, securities, refresh=True, now=None)`
- `QuoteService.read_cached(session, securities, now=None)`
- `GET /api/v1/quotes?symbols=SH:600519,SZ:002594&refresh=false`
- Development startup uses `uv run` with a project-local cache and invokes
  `python -m uvicorn` so the server and dependencies use the same interpreter.
- US securities use an `America/New_York` calendar. Runtime tzdata should be
  installed for daylight-saving correctness; a fixed-offset fallback only keeps
  offline startup from crashing.

### 3. Contracts

`QuoteSnapshot` carries `symbol`, `last_price`, optional `prev_close`,
`change_pct`, `source_time`, `fetched_at`, `provider`, and `is_stale`.
Symbols are normalized to uppercase `MARKET:CODE`. Cache rows preserve the last
valid price and set `is_stale=True` when the TTL expires or refresh fails.
Refreshes are serialized by one async lock and throttled by a minimum interval;
three consecutive provider failures open an exponential backoff circuit.

Only a fresh snapshot whose price reaches an active, alert-enabled target may
set `ProfitTarget.status` to `TRIGGERED`. No quote operation creates trades.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Unknown requested symbol | Return an item with null quote fields and `isStale=true` |
| Cache younger than TTL | Return cached snapshot without provider refresh |
| Cache older than TTL | Return last price with `isStale=true` |
| Provider timeout/error | Preserve last cache value and mark it stale |
| Three consecutive failures | Open circuit and serve stale cache during backoff |
| Fresh price reaches target | Set target to `TRIGGERED` with `triggered_at` |
| Stale price reaches target | Do not trigger the target |

### 5. Good / Base / Bad Cases

- Good: two concurrent refresh requests share one provider call and both receive
  the same cache result.
- Base: an offline provider leaves ledger buy, sell, void, and query routes usable.
- Bad: replacing a failed quote with zero, or using a stale price to trigger an
  alert.
- Windows/offline: if IANA tzdata is unavailable, Asia/Shanghai may safely fall
  back to fixed UTC+8 because this market timezone has no DST transition.

### 6. Tests Required

- Calendar tests cover trading sessions, holidays, and TTL expiry.
- Provider tests cover symbol normalization, change calculation, and fallback.
- Integration tests assert cache persistence, stale fallback, circuit backoff,
  single-flight refresh, and fresh-versus-stale target triggering.
- API tests assert the quote response fields and unknown-symbol behavior.

### 7. Wrong vs Correct

#### Wrong

```python
row.last_price = Decimal("0")
row.is_stale = False
```

#### Correct

```python
row.last_price = previous.last_price
row.is_stale = True
```

A provider failure must preserve the previous valid value and make its age
visible to the caller.

---

## Scenario: A 股真实行情与 Dashboard 一致估值

### 1. Scope / Trigger

- 触发条件：新增 `/api/v1/dashboard`、运行时可切换真实 A 股行情源，或修改行情时间、过期覆盖、批次浮盈与账户汇总契约。
- 该契约跨 provider、缓存、账务服务、HTTP DTO 和首页轮询；行情仍是派生数据，不能阻塞或改写账务事实。

### 2. Signatures

- `create_quote_provider(configuration=None) -> QuoteProvider`
- `EastmoneyQuoteProvider.get_quotes(symbols: Sequence[str]) -> dict[str, QuoteSnapshot]`
- `GET /api/v1/dashboard?accountId=<id>&includeClosed=false&mode=cache|auto|manual`
- 响应采用 camelCase：`accountId`、`asOf`、`marketSessionOpen`、`pollAfterSeconds`、`positions`、`summary`、`quotes`。
- `DashboardPosition` 包含 `quote`、`unrealizedReturnPct`、`lowestPriceGroup`；`DashboardSummary` 包含 `valuationStatus`、`totalOpenPositions`、`quotedOpenPositions`、`staleOpenPositions`。

### 3. Contracts

- `QUOTE_PROVIDER` 只接受 `eastmoney|mock`，默认 `eastmoney`；`QUOTE_TIMEOUT_SECONDS` 必须是有限正数，默认 5 秒。导入模块、健康检查和纯账务请求不得访问行情网络。
- 东方财富源只请求 SH/SZ/BJ 证券，上游市场编号为 SH=`1`、SZ/BJ=`0`；每批最多 100 只，响应和累计请求量必须有上限。
- `lastPrice`、`prevClose`、`changePct` 使用 Decimal 解析；`sourceTime` 来自上游，`fetchedAt` 来自本地成功接收时刻，不能用 `fetchedAt` 覆盖 `sourceTime`。
- `cache` 只读账务和共享缓存；`auto` 在交易时段请求刷新，休市时每 60 秒检查展示状态、最多每小时请求上游一次；缺少行情时可立即获取；`manual` 忽略休市展示间隔但仍受共享节流和熔断约束。
- `valuationStatus` 取值：`fresh`（全部持仓有效）、`stale`（全部持仓有价格但至少一项过期）、`partial`（部分持仓缺价格）、`unavailable`（全部缺价格）、`empty`（无未结清持仓）。
- `partial|unavailable` 时账户 `unrealizedPnl` 和 `unrealizedReturnPct` 必须为 `null`；`empty` 时金额为零、百分比为 `null`；收益率分母不大于零时必须为 `null`。
- 最低价组只比较 ACTIVE BUY 交易、OPEN 批次和剩余数量大于零的记录，范围限定同一账户、证券、策略；以原始成交价 `Trade.price` 为准，合并最低价下的全部批次后计算数量和成本加权估值。
- A 股刷新不得把不支持的美股缓存标记为过期或发送给 A 股上游。失败的行情请求不得影响买卖、作废和账务查询。

### 4. Validation & Error Matrix

| 条件 | 结果 |
|---|---|
| 行情源超时、格式异常或熔断开启 | 保留最后有效价格，标记 `stale`；无缓存时为 `null` |
| 多批请求仅部分批次成功 | 保留成功报价，缺失证券保留旧缓存并标记过期 |
| 所有批次均失败或全部返回空 | 不重置熔断失败状态，进入过期缓存回退 |
| 上游单行价格、昨收或身份无效 | 跳过该行，不生成伪造报价 |
| 旧缓存价格为零或负数 | 视为无行情，不得序列化为可估值的零价格 |
| 未结清持仓缺少任一报价 | `partial` 或 `unavailable`，账户总浮盈为 `null` |
| 剩余成本小于或等于零 | 收益率返回 `null`，不得返回 NaN 或 Infinity |
| 旧行情达到止盈价 | 不得触发提醒；仅有效且未过期快照可触发 |

### 5. Good/Base/Bad Cases

- Good：三个同价批次买入价均为 8，其中两个 OPEN 剩余、一个已结清；组只合并两个剩余批次，并按合计剩余成本计算组收益率。
- Base：东财不可达但 SQLite 账务正常；首页返回账务事实、最后报价和明确的过期状态。
- Bad：用当日最低价、含费单位成本或时间最早的一条同价批次代替“最低原始买入成交价下全部剩余批次”。
- Bad：缺一项行情时仍返回其余持仓小计作为完整账户未实现收益。

### 6. Tests Required

- provider 单元测试断言市场映射、Decimal 精度、零涨幅、报价时间、无效行、批量上限、响应上限、多批部分成功和全部失败。
- collector 集成测试断言部分缺失完整回退、空结果不重置熔断、A 股刷新不污染美股缓存、旧行情不触发提醒。
- dashboard 服务测试断言最低价同价全批次合并、部分卖出、结清/作废、账户/证券/策略隔离、零/负成本和五种覆盖状态。
- HTTP 测试断言真实序列化的 camelCase 字段、Decimal 字符串、`null` 与零语义及 `cache|auto|manual` 参数。
- 自动化测试必须注入 provider/transport/clock，不得依赖公网。

### 7. Wrong vs Correct

#### Wrong

```python
lowest_lot = min(open_lots, key=lambda lot: lot.buy_trade.price)
return latest_price * lowest_lot.remaining_qty - lowest_lot.remaining_cost
```

#### Correct

```python
lowest_price = min(lot.buy_trade.price for lot in eligible_lots)
selected = [lot for lot in eligible_lots if lot.buy_trade.price == lowest_price]
remaining_qty = sum(lot.remaining_qty for lot in selected)
remaining_cost = sum(lot.remaining_cost for lot in selected)
return latest_price * remaining_qty - remaining_cost
```

---

## Scenario: 场外开放式基金（OF）已公布单位净值与近期净值曲线

### 1. Scope / Trigger

- 触发条件：新增或修改场外开放式基金（`market="OF"`、`security_type="FUND"`）查名、已公布单位净值行情、缓存新鲜度日历或详情页近期净值曲线。
- 场外基金复用 `CNY` 账户与 `quote_cache` 共享缓存表，但不参与股票盘中逐笔刷新。

### 2. Signatures

- `MARKET_CURRENCY["OF"] = "CNY"`；`is_open_fund_market(market: str | None) -> bool`
- `OpenFundCalendar(nav_ttl_seconds=6 * 3600)`：`is_session_open()` 恒返回 `False`
- `EastmoneyFundQuoteProvider.get_quotes(symbols: Sequence[str]) -> dict[str, QuoteSnapshot]`
- `CompositeQuoteProvider(providers, name="eastmoney")`：按子源 `supports_symbol` 路由合并
- `GET /api/v1/securities/lookup`：候选返回 `securityType`（`"STOCK"` / `"FUND"`）
- `GET /api/v1/securities/{security_id}/intraday`：场外基金返回 `seriesKind="nav"` 与按日期升序排列的近期已公布单位净值点

### 3. Contracts

- **市场与类型**：`POST /api/v1/securities` 当 `market="OF"` 且未显式指定类型（默认 `"STOCK"`）时自动规范为 `security_type="FUND"`。
- **查名区分场内 ETF 与场外基金**：`SecurityInfoProvider.search()` 必须先识别 `MktNum in {"0", "1"}` 的沪深北场内证券（含场内股票与场内 ETF）与美股，再将剩余 6 位数字且属于基金分类的记录归入 `OF` / `FUND`。
- **仅采信已公布单位净值**：`EastmoneyFundQuoteProvider` 与 `SecurityInfoProvider.fund_nav_history()` 从东方财富公开接口 `https://api.fund.eastmoney.com/f10/lsjz`（带 `Referer: https://fundf10.eastmoney.com/`）读取 `DWJZ`、`FSRQ`、`JZZZL`，使用 `Decimal` 解析并跳过空值/非正数，不得使用盘中估算净值替代。
- **独立容错与新鲜度**：`CompositeQuoteProvider` 中股票源与基金源互不阻塞，单源失败不丢弃另一源成功报价；`QuoteService` 对 `OF` 市场使用 `OpenFundCalendar`，避免盘中按分钟级把已公布净值误判过期。
- **前端展示约定**：场外基金份额显示 2~4 位小数、单位显示“份”、净值与成本显示 4 位小数；详情页识别 `seriesKind="nav"` 后横轴按 `MM-DD` 渲染且禁用 3 秒定时轮询。

### 4. Tests Required

- 单元测试（`backend/tests/unit/test_open_fund_market_data.py`）：覆盖 `OF` 市场/日历、`EastmoneyFundQuoteProvider` 正常解析/缺省涨跌幅计算/无效行/部分失败/全失败/响应超限、`CompositeQuoteProvider` 混合路由与单源容错、`SecurityInfoProvider` 场内 ETF 与场外基金区分及 `fund_nav_history` 升序与 5 分钟缓存。
- 集成测试（`backend/tests/integration/test_open_fund_api.py`）：覆盖查名、建档、小数份额与四位净值买卖配对、`seriesKind="nav"` 与 `Dashboard` 估值端到端闭环。
