# 技术设计：场外宽基基金盘中指数估算

## 1. 架构原则与核心边界

本设计遵循项目既有行情契约（参见 `.trellis/spec/backend/market-data-guidelines.md`）：

1. **原始缓存保持纯净**：`quote_cache` 表中，`OF` 基金行**只存官方已公布单位净值**，参考标的（指数/ETF）行**只存上游真实实时行情**。盘中估算净值在读取与聚合层实时派生，绝不覆盖官方净值缓存。
2. **解绑即复原**：因为估算值不落库污染 `quote_cache`，用户修改 `proxy_symbol`、`tracking_ratio` 或解绑后，下一次读取即刻按新配置精确重算。
3. **止盈固化隔离**：`QuoteService.upsert()` 写入的是原始真实行情并调用 `evaluate_targets()`；派生估算快照不经 `upsert()` 写回数据库，因此天然保证**盘中估算价不会误触发数据库 `ProfitTarget.status = TRIGGERED`**。
4. **严格 `Decimal` 运算**：所有价格、涨跌幅、仓位系数均采用 `Decimal` 与 `round_price` / `round_amount` 量化，禁止浮点运算。

---

## 2. 数据模型与增量迁移

### 2.1 `securities` 表新增字段（[ledger.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/models/ledger.py)）

在 `Security` ORM 模型上新增 3 个可选列（仅 `OF` 场外基金使用，其余证券保持默认值/空）：

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| `proxy_symbol` | `String(32)` | 是 | `None` | 参考标的规范代码，格式 `SH:000300` / `SZ:399006` / `SH:510300` |
| `proxy_name` | `String(128)` | 是 | `None` | 参考标的名称快照，如 `沪深300`，便于列表直接展示 |
| `tracking_ratio` | `ExactDecimal(6)` | 否 | `Decimal("0.950000")` | 估算仓位/跟踪系数，有效范围 `(0, 1.5]` |

### 2.2 Alembic 迁移策略
- 新增增量 migration 脚本，仅执行 `op.add_column("securities", ...)`（带 `server_default="0.950000"` 以兼容 SQLite 已有行）。
- 不删表、不重建、不清空本地 `ledger.db` 中的任何业务数据。

---

## 3. 行情源与指数支持

### 3.1 查名支持 A 股指数（[security_info.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/security_info.py)）
- 东方财富 `searchapi.eastmoney.com/api/suggest/get` 返回沪深指数时：
  - `Code` 为 6 位数字，`MktNum` 为 `"1"`（沪市指数如 `000300`、`000905`、`000852`、`000016`、`000688`）或 `"0"`（深市指数如 `399006`、`399001`）。
  - `SecurityTypeName` 包含 `"指数"` 或 `Classify == "INDEX"` 时，将候选 `security_type` 标记为 `"INDEX"`，市场仍归属 `"SH"` / `"SZ"`。
  - 注意：需实测验证东方财富对中证指数（如 `000905`、`000852`、中证A500 `000510`）返回的 `MktNum` 与 `push2` / `trends2` 的 `secid` 前缀（通常沪市/中证为 `1.xxxxxx` 或 `2.xxxxxx`，深市为 `0.xxxxxx`）；若存在 `MktNum == "2"` 的中证指数，在 `EastmoneyQuoteProvider` 与 `_intraday_eastmoney` 中通过市场码映射或支持规则兼容（保持公开代码简洁可读）。

### 3.2 批量行情采集附带拉取参考标的（[collector.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/collector.py)）
- 当 `QuoteCollector.refresh()` 收到一组 `securities` 时：
  1. 提取其中所有 `market == "OF"` 且配置了 `proxy_symbol` 的基金，收集去重后的 `proxy_symbols`。
  2. 将 `proxy_symbols` 一并纳入本次 `provider.get_quotes(symbols)` 请求（`EastmoneyQuoteProvider` 原生支持 `SH`/`SZ` 6 位代码批量请求）。
  3. 仅将属于数据库 `Security` 表的快照通过 `QuoteService.upsert()` 持久化；对于未单独建档在 `securities` 表中的 `proxy_symbol`，可：
     - **方案选型**：在用户绑定 `proxy_symbol` 时，若本地 `securities` 表不存在该参考标的，**自动复用/创建一条轻量 `Security` 记录**（`market` + `symbol` + `name` + `security_type="INDEX"|"STOCK"`），从而直接复用 `quote_cache` 共享缓存、节流、熔断、过期判定与并发锁，**零新增缓存表**！
     - 优点：`QuoteCollector`、`QuoteService`、`DashboardService` 全部复用现有 `QuoteCache` 体系，架构高度一致；且不会污染用户的持仓列表或自选列表（持仓来自 `Lot`，自选来自 `WatchlistItem`）。

---

## 4. 盘中估算派生引擎与日期对齐算法

### 4.1 扩展 `QuoteSnapshot`（[provider.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/provider.py)）
在 `QuoteSnapshot` 增加可选派生元数据字段（默认 `False` / `None`，向后完全兼容）：
- `is_estimated: bool = False`
- `official_nav: Decimal | None = None`
- `official_nav_date: str | None = None`（`YYYY-MM-DD`）
- `proxy_symbol: str | None = None`
- `proxy_name: str | None = None`
- `proxy_change_pct: Decimal | None = None`
- `tracking_ratio: Decimal | None = None`

### 4.2 估算合成逻辑（新增于 `QuoteService`）
新增方法 `QuoteService.resolve_effective_snapshot(fund_security, fund_snapshot, proxy_snapshot, *, now)`：

1. **前置检查**：
   - 若 `fund_security.market != "OF"` 或 `fund_security.proxy_symbol` 为空 -> 原样返回 `fund_snapshot`。
   - 若 `fund_snapshot` 为空（连官方净值都没有） -> 返回 `None`。
   - 若 `proxy_snapshot` 为空或 `proxy_snapshot.change_pct is None` -> 返回 `fund_snapshot`（降级回官方净值）。
2. **交易日期对齐（使用 `AShareTradingCalendar.timezone`，即 `Asia/Shanghai`）**：
   - `nav_date = fund_snapshot.source_time.astimezone(SH_TZ).date()`（若为空则无法对齐，降级返回官方净值）。
   - `proxy_time = proxy_snapshot.source_time or proxy_snapshot.fetched_at`
   - `proxy_date = proxy_time.astimezone(SH_TZ).date()`
   - 若 `nav_date >= proxy_date`：说明官方净值已更新至参考标的最新交易日，**直接返回官方 `fund_snapshot`**（`is_estimated=False`，附带 `proxy_symbol`/`proxy_name`/`tracking_ratio` 元数据供前端展示绑定状态）。
3. **盘中估算计算（`nav_date < proxy_date`）**：
   - `ratio = fund_security.tracking_ratio or Decimal("0.95")`
   - `estimated_change_pct = round_price(proxy_snapshot.change_pct * ratio)`
   - `estimated_price = round_price(fund_snapshot.last_price * (Decimal("1") + estimated_change_pct / Decimal("100")))`
   - 若 `estimated_price <= 0` -> 降级返回官方 `fund_snapshot`。
   - 返回新的派生 `QuoteSnapshot`：
     - `symbol = fund_snapshot.symbol`
     - `last_price = estimated_price`
     - `prev_close = fund_snapshot.last_price`
     - `change_pct = estimated_change_pct`
     - `source_time = proxy_snapshot.source_time`
     - `fetched_at = proxy_snapshot.fetched_at`
     - `provider = f"{fund_snapshot.provider}+proxy"`
     - `is_stale = proxy_snapshot.is_stale`
     - `market_session = proxy_snapshot.market_session`
     - `is_estimated = True`
     - `official_nav = fund_snapshot.last_price`
     - `official_nav_date = nav_date.isoformat()`
     - `proxy_symbol = fund_security.proxy_symbol`
     - `proxy_name = fund_security.proxy_name`
     - `proxy_change_pct = proxy_snapshot.change_pct`
     - `tracking_ratio = ratio`

### 4.3 估算分时曲线换算（[market.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/v1/market.py) & [security_info.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/security_info.py)）
- `/api/v1/securities/{security_id}/intraday` 增加可选查询参数 `series: Literal["auto", "nav"] = "auto"`：
  - 当 `security.market == "OF"` 且配置了 `proxy_symbol` 且 `series == "auto"`：
    1. 读取基金官方净值（缓存优先，缺失时拉取 `fund_nav_history`）。
    2. 拉取 `proxy_symbol` 的当日分钟分时 `proxy_intraday = await intraday_service.read(proxy_symbol)`。
    3. 若 `nav_date < proxy_intraday.trading_date` 且 `proxy_intraday.points` 非空且 `proxy_intraday.quote.prev_close > 0`：
       - 对每个分钟点 `p`：
         - `point_change_pct = (p.price - proxy_prev_close) / proxy_prev_close * 100`
         - `est_nav = round_price(official_nav * (1 + point_change_pct * ratio / 100))`
       - 返回 `seriesKind = "estimated_intraday"`，横轴为当日分钟时间 `HH:MM`，纵轴为换算后的估算净值，并附带派生后的 `quote`。
    4. 若今日休市无分时、参考标的分时失败或今日官方净值已公布，则自动回退返回 `seriesKind = "nav"`（近 30 日官方净值曲线）。
  - 当 `series == "nav"` 时，始终返回近 30 日官方净值曲线，方便用户随时切换对比。

---

## 5. API 契约

### 5.1 绑定/修改/解绑参考标的接口
- `PATCH /api/v1/securities/{security_id}/proxy`
- 请求体：
  ```json
  {
    "proxySymbol": "SH:000300",
    "proxyName": "沪深300",
    "trackingRatio": "0.95"
  }
  ```
  - 解绑时传 `{"proxySymbol": null}`。
  - `trackingRatio` 可省略，默认 `"0.95"`；若传入需校验 `0 < ratio <= 1.5`。
  - 校验目标 `security` 必须为 `market == "OF"`，否则返回 `422`。
  - 校验 `proxySymbol` 必须为合法 A 股代码（`SH:`/`SZ:`/`BJ:` + 6 位数字），并在本地 `securities` 表确保存在对应记录以便共享 `quote_cache`。
- 响应体：返回更新后的 `SecurityRead`（含 `proxySymbol`、`proxyName`、`trackingRatio`）。

### 5.2 扩展响应 DTO（向后兼容新增可选字段）
- `SecurityRead` 新增：`proxySymbol`, `proxyName`, `trackingRatio`。
- `QuoteRead`（看板、批次、单查、分时统一）新增：`isEstimated`, `officialNav`, `officialNavDate`, `proxySymbol`, `proxyName`, `proxyChangePct`, `trackingRatio`。
- `IntradayRead.seriesKind` 扩展为 `'intraday' | 'estimated_intraday' | 'nav'`。

---

## 6. 前端交互设计

1. **绑定配置弹窗/控件（`SecurityDetailPage.vue`）**：
   - 当当前证券为场外基金（`isFund`）时，在头部或行情区域展示当前绑定的参考标的标签（如「跟踪：沪深300 (SH:000300) · 95%」或「未绑定跟踪指数」）及「设置跟踪标的」按钮。
   - 弹窗内复用 `api.lookupSecurities`：用户输入 6 位代码或名称（如 `000300`、`510300`、`沪深300`），实时下拉展示候选，选中后自动带出 `proxySymbol` 与 `proxyName`，默认仓位系数 `0.95`（折叠/次要输入框可微调），点保存调用 `PATCH /securities/{id}/proxy` 并立即刷新行情与批次。
   - 提供「清除绑定」按钮一键解绑。
2. **统一盘中展示（`QuoteBadge.vue`、`DashboardPage.vue`、`SecurityDetailPage.vue`、`IntradayChart.vue`）**：
   - `QuoteBadge.vue`：当 `quote.isEstimated` 为 `true` 时，状态标签显示 `估`（类型 `info` 或 `primary`），tooltip/副标题展示「参考 {proxyName} {proxyChangePct} × {trackingRatio}% · 昨净值 {officialNav} ({officialNavDate})」。
   - `SecurityDetailPage.vue`：当场外基金已绑定 `proxySymbol` 时，盘中**启用与股票一致的 3 秒轮询**（解绑或无绑定时仍禁用轮询）；图表区域支持在「盘中估算分时」与「近 30 日净值」之间一键切换。
   - `IntradayChart.vue`：识别 `seriesKind === 'estimated_intraday'` 时按当日 `HH:MM` 渲染分钟曲线（与 `intraday` 样式一致），纵轴保留 4 位净值精度。
   - 止盈提示：`LotTable.vue` / `SecurityDetailPage.vue` 已根据 `currentQuote.lastPrice` 计算浮盈与止盈距离，盘中估算达标时清晰标注「预估达标」。

---

## 7. 风险与回滚考量

- **中证/沪深指数代码前缀差异**：实施第一步先对常见宽基指数（`000300`、`000905`、`000852`、`000016`、`399006`、`000688`、`000510`）做东财接口契约核验，确认 `MktNum` 与 `secid` 映射无遗漏。
- **回滚方案**：若需回退，前端移除配置入口、后端停用估算派生逻辑即可立即恢复纯官方净值模式；数据库新增的 3 个可空/带默认列不影响旧版本逻辑。
