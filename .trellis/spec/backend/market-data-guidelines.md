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

---

## Scenario: 美股（US）公开批量实时行情、盘前/盘后扩展时段与多账户日历

### 1. Scope / Trigger

- 触发条件：修改美股批量行情源、美股盘前/盘后交易日历、美股分时曲线（`iscr=1`）或 Dashboard 按账户币种选择交易日历的逻辑。
- 美股复用 `USD` 账户与共享 `quote_cache` 表，零数据库表结构变更，与 A 股和场外基金在 `CompositeQuoteProvider` 中按市场路由且独立容错。

### 2. Signatures

- `MarketSessionPhase = Literal["pre", "regular", "post", "closed"]`
- `USTradingCalendar.session_phase(value=None) -> MarketSessionPhase`：盘前 `04:00–09:30 ET`、常规盘 `09:30–16:00 ET`、盘后 `16:00–20:00 ET`、其余休市；`is_session_open()` 在 `pre`/`regular`/`post` 均返回 `True`。
- `USMarketResolver`：线程安全有界 LRU 缓存 + 东方财富 `suggest` 精确匹配 `105/106/107` 交易所编号。
- `TencentUSQuoteProvider` & `EastmoneyUSQuoteProvider` & `FallbackUSQuoteProvider`：美股默认优先腾讯公开批量接口（`qt.gtimg.cn/q=us...`，无需交易所编号、覆盖 VOO/SPY 等美股 ETF），缺失或失败时自动回退东方财富美股源。
- `QuoteSnapshot.market_session` / `QuoteRead.marketSession` / `DashboardRead.marketSession`

### 3. Contracts

- **盘前/盘后报价选取**：在美东盘前或盘后时段，若 `ulist.np/get` 返回有效正数扩展价 `f251`，优先作为当前活跃 `last_price` 并标注 `market_session="pre"|"post"`；若 `f253` 缺失则按参考收盘价精确计算涨跌幅；无扩展成交时平滑回退常规价 `f2`，绝不伪造价格。
- **测试隔离原则**：向 `EastmoneyUSQuoteProvider` 或 `SecurityInfoProvider` 显式注入自定义 `transport` 且未指定 `resolver` 时，必须使用独立的 `USMarketResolver` 实例，避免污染进程级共享缓存。
- **美股分时与跨午夜**：`SecurityInfoProvider.intraday()` 对 `US:*` 传 `iscr=1` 拉取盘前/盘后分钟点；东财时间字符串先按 `Asia/Shanghai` 解析转 UTC，再按 `America/New_York` 归属美股交易日。
- **Dashboard 账户日历感知**：`DashboardService.build()` 按 `account.currency` 选择交易日历（`USD` 使用 `USTradingCalendar`，`CNY` 使用 `AShareTradingCalendar`），与 A 股保持一致的 10s/60s 轮询契约。

### 4. Tests Required

- 单元测试（`backend/tests/unit/test_us_market_data.py`）：覆盖美股四时段日历、`USMarketResolver` 缓存与精确匹配、盘前/盘中/盘后快照解析与回退、未知市场码 `105/106/107` 探测、三市场组合路由、美股分时 `iscr=1` 与跨北京午夜归属。
- 集成测试（`backend/tests/integration/test_us_stock_api.py`）：覆盖创建 USD 账户、美股建档、碎股小数含费买卖配对、美股盘前 Dashboard 估值与 10s 轮询、按账户筛选流水/复盘汇总及美股盘前分时接口。

---

## Scenario: 场外宽基基金（OF）盘中指数参考估算与估算分时

### 1. Scope / Trigger

- 触发条件：修改 `Security.proxy_symbol` / `proxy_name` / `tracking_ratio`、`PATCH /api/v1/securities/{id}/proxy`、`QuoteService.resolve_effective_snapshot()` 或场外基金 `seriesKind="estimated_intraday"` 换算逻辑。
- 仅适用于纯 A 股宽基场外基金（`market="OF"`）绑定 A 股指数或场内 ETF（`SH`/`SZ`/`BJ` 6 位数字代码）。

### 2. Signatures

- `PATCH /api/v1/securities/{security_id}/proxy`：绑定、修改或清除（`proxySymbol: null`）参考标的，默认 `trackingRatio = 0.950000`，范围 `(0, 1.5]`。
- `QuoteService.resolve_effective_snapshot(security, snapshot, proxy_snapshot) -> QuoteSnapshot | None`
- `GET /api/v1/securities/{security_id}/intraday?series=auto|nav`：支持 `seriesKind="estimated_intraday"` 与 `seriesKind="nav"` 切换。

### 3. Contracts

- **缓存纯净与解绑即复原**：`quote_cache` 中场外基金行仅保存官方已公布单位净值，参考标的另行共享 `quote_cache`；盘中估算净值仅在读取与聚合层实时派生，绝不覆盖官方净值缓存。
- **日期对齐防重复计算**：统一按 `Asia/Shanghai` 比较官方净值日期与参考标的交易日；仅当 `nav_date < proxy_date` 时按 `estimated_change_pct = proxy_change_pct * tracking_ratio`、`estimated_price = official_nav * (1 + estimated_change_pct / 100)` 派生；`nav_date >= proxy_date` 时自动切回官方净值（`is_estimated = False`）。
- **止盈固化边界**：`QuoteService.evaluate_targets()` 仅由 `upsert()` 写入的官方净值驱动；盘中估算触达止盈价仅在前端高亮“预估达标”，不得改写数据库 `ProfitTarget.status = TRIGGERED`。
- **降级原则**：参考标的缺失或行情失败时平滑回退官方净值，不生成零价或阻断展示。

### 4. Tests Required

- 单元测试（`backend/tests/unit/test_fund_proxy_estimate.py`）：覆盖指数查名、精确估算与当日官方净值回切、参考标的缺失降级、采集器附带刷新与止盈不误固化。
- 集成测试（`backend/tests/integration/test_fund_proxy_api.py`）：覆盖绑定校验与解绑、Dashboard/Lots 估算估值、`series=auto|nav` 估算分时换算与历史净值切换。

---

## Scenario: 长桥实时推送行情、进程级内存行情中心（MarketDataHub）与 SSE 事件流

### 1. Scope / Trigger

- 触发条件：修改 `quote.provider=longbridge` 配置、`LongbridgeQuoteStream` / `LongbridgeQuoteProvider`、`MarketDataHub` 内存缓存与落盘策略、或 `GET /api/v1/quotes/stream` SSE 推送协议。
- 行情依然是派生展示数据，任何上游推流断连、凭据过期或 SSE 中断均不得阻塞账务写操作或污染账务事实。

### 2. Signatures

- `SUPPORTED_QUOTE_PROVIDERS = frozenset({"eastmoney", "longbridge", "mock"})`；`quote.longbridge.{app_key,app_secret,access_token,enable_overnight,http_url,quote_ws_url}` 遵循 `YAML > env > 默认值`，`provider=longbridge` 时三元组必填。
- `to_longbridge_symbol(canonical) -> str | None` / `from_longbridge_symbol(lb_symbol) -> str | None`：仅映射 `SH`/`SZ`/`US`，`BJ`/`OF` 返回 `None` 并由东方财富/腾讯备源补齐。
- `LongbridgeQuoteStream`：后台线程运行 `QuoteContext`，通过 `loop.call_soon_threadsafe(...)` 桥接主事件循环；单连接订阅上限 500 只（按持仓 > proxy > 自选 > 临时查看优先级裁剪），断线采用指数退避（5s -> 10s -> 20s -> 60s）自动重连。
- `MarketDataHub`：由 FastAPI `lifespan`（`create_tables=True` 时）启停；维护 `_quotes`（原始报价，不落盘估算值）、300ms 节流合并广播、30s Write-behind 异步落盘（`evaluate_targets=False`）与停机 flush。
- `GET /api/v1/quotes/stream?symbols=...&accountId=...`：复用 Cookie 鉴权（未登录 401），输出 `text/event-stream; charset=utf-8` 与 `X-Accel-Buffering: no`，推送 `event: snapshot`、`event: quote`、`event: status` 与每 25 秒 `: heartbeat`。

### 3. Contracts

- **基线与增量合并**：长桥 `PushQuote` 不含 `prev_close`，新增订阅必须先调 `ctx.quote(added)` 缓存 `LongbridgeBaseline`（含美股 `regular_close`），再用 `Decimal` 精确计算涨跌幅并过滤非正数报价；长桥 Python SDK 返回的无时区 `datetime` 为系统本地时区，必须通过 `value.astimezone(UTC)` 还原为 UTC，严禁 `.replace(tzinfo=UTC)` 导致时段与 `sourceTime` 偏移。
- **内存优先与零阻塞读取**：`QuoteService._read_raw_cached()` 优先合并内存中心与 `quote_cache` 中更新的快照；当 `MarketDataHub.is_push_active()` 且全部受支持标的均有未过期内存快照时，`DashboardService._load_snapshots(mode="auto")` 直接零阻塞返回内存快照。
- **场外基金估算联动**：`MarketDataHub.publish()` 收到参考标的报价更新时，在内存中 `O(k)` 即时重算绑定场外基金的有效估算快照并随同广播；落盘仅写入原始官方净值与参考标的行情，绝不污染 `quote_cache`。
- **优雅停机与测试隔离**：`MarketDataHub.stop()` 先向全部在线 SSE 订阅者发送 `__close__` 退出信号再刷盘，防止长连接阻塞停机；`create_app(create_tables=False)` 测试模式不自动连接本地 `ledger.db` 或公网推流。

### 4. Tests Required

- 单元测试：`backend/tests/unit/test_config.py`（长桥配置与校验）、`backend/tests/unit/test_longbridge_adapter.py`（代码映射、基线/增量合并、美股盘后基准、500 上限与线程桥接）、`backend/tests/unit/test_quote_hub.py`（内存发布、基金代理联动估算、300ms 合并、落盘纯净与临时订阅引用计数）。
- 集成测试：`backend/tests/integration/test_quote_stream_api.py`（未登录 401、首帧 `snapshot`、增量 `quote`、多用户隔离及内存优先 Dashboard 估值）。

---

## Scenario: 个股近 20 个已收盘交易日平均振幅（`security_daily_metrics`）

### 1. Scope / Trigger

- 触发条件：修改日 K 线振幅计算（`backend/app/market_data/amplitude.py`）、`security_daily_metrics` 缓存表或 `DashboardPositionRead` 的 `avgAmplitude20d` / `avgAmplitudeDays` / `avgAmplitudeAsOf` 字段。
- 振幅属于只读派生指标，任何上游拉取失败或超时均不得阻断或报错 `GET /api/v1/dashboard`。

### 2. Contracts

- **口径与窗口**：单日振幅为 `(最高价 - 最低价) / 昨收 × 100%`（优先采信上游标准振幅字段或前复权常规时段日 K 精确 `Decimal` 计算），严格排除当日盘中未收盘 K 线，并过滤相邻日 K 间隔超过 30 个自然日的异常断档/占位行；取最近最多 20 个已收盘交易日的算术均值（不足 20 日按实际已收盘天数求平均）。
- **市场与数据源路由**：支持 A 股（`SH`/`SZ`/`BJ`）与美股（`US`）；场外基金（`OF`）直接跳过并返回 `null`。
  - 当 `quote_provider == "longbridge"` 时，复用 `LongbridgeQuoteProvider.get_daily_candlesticks()`（`Period.Day` + `AdjustType.ForwardAdjust` + `TradeSessions.Intraday`）作为 `SH`/`SZ`/`US` 主源（`longbridge-kline`），失败或空结果时自动降级回退；
  - 美股备源优先调用腾讯美股日 K 接口（按 `105->.OQ` / `106->.N` / `107->.AM` 交易所后缀映射与探测，命中后写回 `USMarketResolver`），失败时再回退东方财富美股日 K；A 股备源调用东方财富日 K。
- **缓存与防打爆**：按各市场理论最新已收盘交易日判断 `security_daily_metrics` 新鲜度，同一已收盘交易日内零上游请求；进程内维护 30 分钟失败/节假日冷却与并发上限（4）、批量超时（4s）；`quote_provider == "mock"` 与 `mode == "cache"` 下严禁访问公网。
