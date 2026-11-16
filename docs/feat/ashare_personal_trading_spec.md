# A 股个人交易管理与盯盘系统 — SPEC

- **版本**：v0.1（可实施草案）
- **日期**：2026-11-05
- **项目代号**：A-Share Portfolio Journal
- **目标用户**：单个个人投资者（预留多账户能力）
- **部署方式**：自托管 Web 应用
- **核心原则**：交易账本正确性优先于行情实时性；不接券商交易接口、不自动下单。

---

## 1. 产品目标

构建一个以 **A 股交易流水** 为可信数据源、兼具自选股行情展示能力的个人投资管理系统。用户可以按股票多次买入、分批卖出，将卖出数量与指定买入批次配对，查看每一批次/交易周期的实际收益，并按目标收益率设置提醒。已经完全抵消的买卖在日常持仓视图中默认折叠，但永久保留在历史账本和收益统计中。

### 1.1 成功标准

1. 手动录入同一股票的多笔买入和卖出，剩余数量、成本、已实现/未实现盈亏完全一致。
2. 一笔卖出可以匹配多个买入批次；一个买入批次可以被多笔卖出匹配。
3. 已结清批次默认隐藏；切换“显示已结清”可复原查看，且折叠状态不影响收益。
4. 能为未结清买入批次设置分段止盈目标及数量，显示参考目标价和触发状态；不自动执行交易。
5. 自选股展示最新价、涨跌幅、数据时间和过期状态；行情获取失败不破坏交易账本。
6. 对每笔历史交易可追溯来源及变更；导入同一份流水不会重复记账。

### 1.2 约束与假设

- 第一阶段针对 **沪深 A 股普通股票**；ETF 可作为后续增强，场外基金、港股、美股不纳入 MVP。
- 记账货币：CNY；行情与成交时间：`Asia/Shanghai`，数据库统一采用带时区时间戳或 UTC 存储。
- 不持有券商账号密码、不使用交易 API、不提供自动买卖或收益保证。
- MVP 单用户、本地或家庭内网使用；如公网访问，必须启用登录鉴权、HTTPS 和备份。
- 实盘真实费用以用户录入的成交回单为准；费用预估不能替代实盘账单。

---

## 2. 范围与优先级

| 编号 | 功能 | 版本 | 验收要点 |
|---|---|---|---|
| F-01 | 股票基础信息、搜索、自选股 | MVP | 添加/删除/排序自选股 |
| F-02 | 手动录入买卖成交、费用 | MVP | 支持日期、价格、数量、费用、备注 |
| F-03 | 按批次形成持仓（Lot） | MVP | 每笔买入形成独立批次 |
| F-04 | 指定买卖配对、FIFO 默认配对 | MVP | 一卖多买、一买多卖、部分成交均正确 |
| F-05 | 已结清批次折叠/展开 | MVP | 仅影响显示、不影响统计 |
| F-06 | 剩余数量、成本与盈亏核算 | MVP | 统一计算口径、可复算 |
| F-07 | 每批次分段目标收益/计划卖出量 | MVP | 目标价预估、目标状态 |
| F-08 | 自选股行情与手动刷新 | MVP | 失败回退为缓存，显示时间和过期提示 |
| F-09 | 个股详情与交易标记 | V1 | K 线叠加买卖记录 |
| F-10 | 交易周期（Cycle）自动归档 | V1 | 清仓后结算周期，下次建仓开启新周期 |
| F-11 | CSV/Excel 导入、预览、去重 | V1 | 可配置字段映射、不可重复提交 |
| F-12 | WebSocket/SSE 行情推送与预警 | V1 | 同一行情源统一采集，订阅推送 |
| F-13 | 分红、送股、拆分、配股处理 | V1 | 公司行动事件可审计、成本/数量一致 |
| F-14 | 账户/策略分组及收益分析 | V1 | 网格仓、长线仓分别统计 |
| F-15 | 月/年收益、交易胜率、回撤 | V2 | 指标定义可解释，非交易指标不混用 |
| F-16 | 全市场扫描/Level-2/云同步 | 非目标 | 另立需求评估数据许可和成本 |

> **建议实施次序**：先验证账本与配对引擎，再实现 UI；行情模块最后接入。MVP 不依赖任何实时行情供应商即可正确核算历史交易。

---

## 3. 关键概念和状态

- **Account（账户）**：用户在不同券商/资金账户中的逻辑账本；MVP 默认一个账户。
- **Security（证券）**：`market + symbol` 唯一标识，例如 `SH:600519`、`SZ:002594`；不能仅用名称识别。
- **Trade / Fill（成交）**：实际发生的一次买入或卖出及其费用。一个委托发生多笔成交时可拆为多个 Fill。
- **Lot（买入批次）**：由一笔买入成交产生的成本批次，不随卖出被删除。
- **TradeMatch（买卖配对）**：把某笔卖出的指定数量分配给某个买入批次的不可重复计量记录。
- **Position（当前持仓）**：由交易和配对**计算得出**的视图，不是手动维护的真相源。
- **Cycle（交易周期）**：同一账户、证券、策略下从净持仓 0 开始建仓，到再次归零的操作集合。
- **Target（止盈计划）**：预期收益率、计划出售数量、参考价和提醒规则；不是实际成交。
- **Settled / Closed（已结清）**：某批次的可用剩余数量为 0；不等于删除记录。

### 3.1 交易状态

- Lot：`OPEN`（未全卖）、`CLOSED`（已全卖）。
- Cycle：`OPEN`（该周期尚有持仓）、`CLOSED`（已清仓）。
- Target：`ACTIVE`、`TRIGGERED`、`COMPLETED`、`CANCELLED`；行情触发目标只能改变提醒状态，不能自动记作成交。
- Trade：`ACTIVE`、`VOIDED`；更正采用审计撤销/重建，不做无记录的覆盖修改。

---

## 4. 主要用户流程

### 4.1 分批买入

1. 选择股票及账户，录入买入时间、数量、成交单价和费用。
2. 后端校验数据；提交后产生 `BUY Trade` 和对应 Lot。
3. Lot 以含买入费用的总成本进入仓位计算，可设置独立目标。

### 4.2 分批卖出及买卖抵消

1. 录入卖出时间、股数、成交价和费用。
2. 展示当前可匹配 Lot 及各 Lot 的可配数量；默认提供 FIFO 预配对结果。
3. 用户可调整为手动配对（例如：卖出 600 股，分别匹配 A 批次 200 股、B 批次 400 股）。
4. 提交时在**单个数据库事务**中保存卖出成交及全部 TradeMatch，并校验总匹配数等于卖出数量、每个 Lot 不超额。
5. 剩余数为 0 的 Lot 变为已结清；日常视图默认不展示，但历史记录、收益和导出仍可用。
6. 不允许直接把卖出价格改成目标价；止盈计划与实际成交严格分离。

### 4.3 已结清隐藏

- 默认“当前持仓”只显示 `remaining_qty > 0` 的 Lot。
- “显示已结清”开关显示已结清 Lot 和其配对明细。
- 个股页面汇总显示“已结清 X 批、已实现收益 ¥Y”，防止隐藏后误以为收益丢失。
- 历史交易列表默认全部保留，可按状态筛选；导出不随 UI 折叠过滤，除非用户显式选择导出范围。

### 4.4 目标收益/分段卖出计划

- 可以对每个未结清 Lot 配置多档目标：`target_return_pct`、`planned_sell_qty`、`basis`（按单位含费成本 / 按净收益目标）、`alert_enabled`。
- 如 Lot 有 1,000 股，可配置 +5% 计划卖 300 股、+10% 卖 400 股，余下 300 股不设目标。
- 计划卖出量总和 **不得超过** 当前剩余股数；实际卖出后应重新计算可执行计划量，对超量计划给出冲突提示。
- 参考目标价根据买入成本和所选费用模型计算；净收益目标采用求解方式考虑最低佣金等非线性费用，不把预估价格当保证成交价。
- 触发条件为最新有效报价达到阈值，报价过期时不触发新提醒；允许手动关闭提醒。

### 4.5 交易周期

- 一个 `account + security + strategy` 下，第一次从 0 买入时创建 Cycle。
- 当已记录成交后的净股数回到 0，Cycle 自动关闭；下一次买入创建新 Cycle。
- Cycle 已关闭时，折叠显示汇总：起止日期、净收益、买卖次数、已实现盈亏。
- 历史成交或配对更正可能影响 Cycle 边界，须事务内从受影响时间点重算并留下审计记录。
- MVP 中一笔卖出只能归于一个策略；若卖出跨策略，录入或导入时先拆成多个逻辑成交。跨策略自动分摊留待 V1。

---

## 5. 计算口径（必须全系统一致）

### 5.1 记账精度

- 数量 `DECIMAL(20,4)`（普通 A 股 UI 可显示整数；预留公司行动产生的小数）。
- 价格 `DECIMAL(20,6)`，金额和费用 `DECIMAL(20,4)`；内部使用 Python `decimal.Decimal`，禁止用二进制 `float` 参与账务。
- 最终 CNY 展示通常保留 2 位小数，计算精度与展示精度分离；剩余分摊尾差落在最后一段配对上，确保总额严格守恒。

### 5.2 买入与卖出现金流

```
buy_gross      = buy_price * buy_qty
buy_cash_out   = buy_gross + buy_fees_total
sell_gross     = sell_price * sell_qty
sell_cash_in   = sell_gross - sell_fees_total
```

费用字段至少包含：佣金、印花税、过户费、其他；为适配不同券商/不同年份规则，按每笔真实成交录入，不硬编码统一费率。

### 5.3 批次成本和已实现收益

```
lot_original_cost = buy_gross + buy_fees_total
lot_unit_cost     = lot_original_cost / buy_qty
matched_buy_cost  = lot_original_cost * matched_qty / buy_qty
matched_sell_net  = (sell_gross - sell_fees_total) * matched_qty / sell_qty
realized_pnl      = matched_sell_net - matched_buy_cost
lot_remaining_qty = buy_qty - SUM(TradeMatch.matched_qty on this lot)
lot_remaining_cost = lot_original_cost - SUM(matched_buy_cost on this lot)
```

费用/成本分摊按原始成交比例进行，尾差归最后一段。**不得**把卖出手续费同时从卖出净收入扣除，又在已实现收益里重复扣一次。

### 5.4 持仓浮盈

```
position_qty            = SUM(open_lot.remaining_qty)
remaining_cost          = SUM(open_lot.remaining_cost)
position_avg_cost       = remaining_cost / position_qty
unrealized_pnl_gross    = position_qty * latest_price - remaining_cost
unrealized_return_gross = unrealized_pnl_gross / remaining_cost
```

浮盈默认**不估算未来卖出手续费**，指标标识“未扣预计卖出费用”。如需要净浮盈，新增另一个显式指标，不可悄悄覆盖。

### 5.5 策略累计回本参考价

对于**尚未清仓的单个 Cycle**：

```
net_unrecovered_cash = SUM(all buy_cash_out)
                     - SUM(all sell_cash_in)
                     - SUM(dividends_received_in_cycle, if enabled)
strategy_breakeven_price = net_unrecovered_cash / remaining_qty
```

该值衡量本轮交易考虑已经回收资金后，剩余股份需要达到的理论整体现金流盈亏平衡价格，**不是剩余股票的会计/税务成本价**。若该值为负，可以显示“资金已全部回收，盈余 ¥X”，但不能以负成本替代持仓成本。

### 5.6 周期收益

```
cycle_pnl_as_of_now = SUM(sell_cash_in)
                    + position_qty * latest_price
                    + SUM(dividends_received_in_cycle, if enabled)
                    - SUM(buy_cash_out)
```

若最新价缺失，则周期当前盈亏应标记为“无法按市值计算”，不能使用过期价伪装为实时结果。已关闭 Cycle 的最终收益不依赖行情。收益率必须显示分母口径，例如“累计买入额收益率（仅参考）”，避免与年化收益/XIRR 混淆。V2 引入 XIRR、时间加权收益率（TWR）等标准指标。

### 5.7 对账不变量

必须满足：

1. `SUM(trade_matches.qty for sell) == sell.qty`（卖出提交完成时）。
2. `SUM(trade_matches.qty for lot) <= lot.original_qty`。
3. `SUM(open lot remaining_qty) == SUM(buy.qty) - SUM(sell.qty)`（同一账户/证券/策略/周期）。
4. `SUM(allocated_buy_cost) + SUM(remaining_lot_cost) == SUM(all_buy_cash_out)`。
5. `SUM(realized_pnl) + SUM(unrealized_pnl_gross) == cycle_pnl_as_of_now`（无分红、行情有效且费用归属一致时；有分红应加入相同口径）。
6. 更改展示筛选/折叠开关，所有账务指标保持不变。

---

## 6. 典型验收案例

**前提：以下不计手续费、分红。**

| 时间 | 成交 | 数量 | 单价 | 配对 |
|---|---|---:|---:|---|
| 11-01 | 买入 A | 1,000 | ¥10.00 | 创建 Lot A |
| 11-03 | 买入 B | 1,000 | ¥9.00 | 创建 Lot B |
| 11-05 | 卖出 S1 | 1,000 | ¥11.00 | 全部匹配 Lot A |

**期望结果**：

- Lot A `remaining_qty = 0`、状态 `CLOSED`、默认隐藏。
- Lot B `remaining_qty = 1000`、`remaining_cost = ¥9000`、单位成本 `¥9.00`。
- 累计已实现收益 `¥1000`。
- 当前周期 `strategy_breakeven_price = (¥19000 - ¥11000) / 1000 = ¥8.00`。
- 切换“显示已结清”，Lot A 可见；上述计算数值不变。

**进一步验收**：

- 将 S1 改为匹配 Lot A 600 股、Lot B 400 股：Lot A 剩 400、Lot B 剩 600；两个 Lot 都不得折叠。
- 将后续 400 股卖出匹配 Lot A：Lot A 归零并折叠；Lot B 仍显示 600 股。
- 若 S1 为 2,100 股，则拒绝录入（不允许无持仓卖出）。
- 对相同导入文件连续点击两次“确认导入”，第二次必须提示重复且账本行数不增加。

---

## 7. 数据模型（建议）

数据库采用 SQLite + SQLAlchemy 2.x + Alembic。下表为逻辑设计，字段允许后续扩展；所有 ID 使用 UUID 或数据库主键加外部稳定 ID。

### 7.1 核心表

**`accounts`**

- `id`, `name`, `broker?`, `currency='CNY'`, `created_at`

**`securities`**

- `id`, `market` (`SH`/`SZ`/`BJ` 预留), `symbol`, `name`, `security_type`, `status`
- 唯一索引 `(market, symbol)`

**`strategies`**

- `id`, `account_id`, `name`（默认 `DEFAULT`，例如 `GRID` / `LONG_TERM`）

**`trades`**

- `id`, `account_id`, `security_id`, `strategy_id`, `cycle_id`
- `side` (`BUY`/`SELL`), `executed_at`, `qty`, `price`, `gross_amount`
- `commission`, `stamp_duty`, `transfer_fee`, `other_fee`, `fees_total`
- `external_trade_id?`, `source` (`MANUAL`/`IMPORT`), `import_batch_id?`
- `memo?`, `status`, `created_at`, `updated_at`, `revision`
- 请求幂等键 `client_request_id?`、必要唯一索引

**`lots`**

- `id`, `buy_trade_id`（唯一）、`account_id`, `security_id`, `strategy_id`, `cycle_id`
- `original_qty`, `original_cost`；当前剩余数量和成本建议按配对动态计算/事务维护缓存且可重建

**`trade_matches`**

- `id`, `sell_trade_id`, `lot_id`, `matched_qty`
- `allocated_buy_cost`, `allocated_sell_net`, `realized_pnl`（可重建的派生快照）
- `created_at`, `matching_method` (`FIFO`/`MANUAL`)
- 索引 `sell_trade_id`、`lot_id`；同一 `(sell_trade_id, lot_id)` 可唯一约束

**`cycles`**

- `id`, `account_id`, `security_id`, `strategy_id`, `opened_at`, `closed_at?`, `status`

**`profit_targets`**

- `id`, `lot_id`, `target_return_pct`, `planned_sell_qty`, `calculation_basis`
- `target_price_estimate`, `alert_enabled`, `status`, `triggered_at?`, `created_at`

**`quote_cache`**（可选择只放内存）

- `security_id`, `last_price`, `prev_close`, `change_pct`, `source_time`, `fetched_at`, `provider`, `is_stale`

**`audit_events`**

- `id`, `entity_type`, `entity_id`, `action`, `before_json`, `after_json`, `actor`, `created_at`, `reason?`

### 7.2 V1 增补表

- `import_batches` / `import_rows`：文件校验和、映射方案、逐行结果及重复指纹。
- `cash_events`：现金分红、其他现金调整，关联账户、证券、周期。
- `corporate_actions`：送股、拆分、配股、除权事件，保持 Lot 数量和成本的审计链。
- `alert_events`：去重提醒、触发价格和行情时间。

**设计原则**：`trades`、`trade_matches`、`cash_events` 为可审计的事实；持仓、收益、状态优先由事实计算，缓存只能是派生数据。

---

## 8. API 契约（REST v1）

统一前缀：`/api/v1`；失败使用标准错误格式：

```json
{"error": {"code": "INSUFFICIENT_LOT_QTY", "message": "可配对数量不足", "details": {}}}
```

| Method | Path | 用途 |
|---|---|---|
| GET | `/securities?query=...` | 股票搜索 |
| GET / POST | `/watchlist` | 自选股列表 / 添加 |
| DELETE | `/watchlist/{securityId}` | 移除自选股 |
| GET | `/quotes?symbols=SH:600519,SZ:002594` | 最新行情（携带时间/过期字段） |
| GET | `/trades?accountId=&securityId=&includeVoided=false` | 交易流水 |
| POST | `/trades/buy` | 新增买入成交并创建 Lot |
| POST | `/trades/sell/preview` | 卖出配对预览（FIFO/手动） |
| POST | `/trades/sell` | 原子提交卖出与匹配 |
| POST | `/trades/{id}/void` | 撤销错误成交（带审计） |
| GET | `/positions?accountId=&includeClosed=false` | 聚合持仓及批次 |
| GET | `/securities/{id}/lots?includeClosed=false` | 个股批次明细 |
| GET | `/lots/{id}/matches` | 查询某买入批次的卖出抵消记录 |
| GET / POST | `/lots/{id}/targets` | 读取/设置收益目标 |
| PATCH / DELETE | `/targets/{id}` | 修改/取消目标 |
| GET | `/analytics/summary?accountId=` | 汇总已实现、浮动收益及现金流 |
| GET | `/cycles?securityId=&includeClosed=false` | 交易周期及归档 |
| POST | `/imports/preview` | V1：预览字段映射、行级校验和去重 |
| POST | `/imports/commit` | V1：幂等确认导入 |
| WS | `/ws/quotes` | V1：订阅行情（或采用 SSE） |

### 8.1 提交卖出的示例请求

```json
{
  "client_request_id": "unique-from-client",
  "account_id": "acc-1",
  "security_id": "sec-1",
  "strategy_id": "strategy-grid",
  "executed_at": "2026-11-05T14:01:00+08:00",
  "qty": "600",
  "price": "11.0000",
  "fees": {"commission": "5.00", "stamp_duty": "3.30", "transfer_fee": "0.00", "other_fee": "0.00"},
  "matches": [
    {"lot_id": "lot-a", "qty": "200"},
    {"lot_id": "lot-b", "qty": "400"}
  ]
}
```

交易金额、配对成本、已实现收益由服务端统一计算并返回；客户端不得直接提交可信盈亏数值。

### 8.2 并发与幂等

- 相同 `client_request_id` 在同一账户范围内重复提交，返回原交易结果，不增加账本记录。
- 提交卖出时使用数据库事务锁和版本校验，保证两次并发卖出不会超额匹配相同 Lot。
- SQLite MVP 使用单写入者事务模式；未来迁移 PostgreSQL 时沿用事务语义。
- 交易更正对受影响的配对/周期做原子重算，禁止只修改展示缓存。

---

## 9. 行情与告警模块

### 9.1 采集策略

- 定义 `QuoteProvider` 抽象接口：`get_quotes(symbols)`、`get_history(symbol, interval)`、`health()`。
- 首选 Python 生态中的 AKShare 适配器，具体接口在实施时验证可用性、限流及服务条款；接口失败时支持后续切换备用 Provider。
- **只为自选股与当前持仓采集**，不要为每个浏览器单独拉取。
- 默认计划：交易时段每 **5–15 秒**刷新一次；若上游本身不提供这个频率，按上游实际能力调整，不声称“交易所实时”。
- 提供 `source_time`（行情源时间）、`fetched_at`（采集时间）与 `is_stale`；缓存超过阈值显示“已过期”。
- 非交易日和休市时减少采集；交易日历按上海交易所/深圳交易所官方安排维护，不靠固定工作日推断。

### 9.2 稳定性

- Provider 限流、超时、错误码的重试与指数退避；多次失败后暂时熔断。
- 上游异常时保留最近有效价格并打上过期标签，不将价格重置为 0。
- 告警在行情无效时禁止新触发；同一档目标设置冷却期，避免重复提醒。
- 记录采集成功率、延迟、上游错误和缓存年龄，便于排查。

### 9.3 数据和许可

- 免费公开行情接口不等于具备稳定服务或再分发授权；MVP 为私人自用。
- 如对外开放、多用户服务或需要 Level-2，应另行取得适用的行情使用和展示授权。

---

## 10. 前端信息架构

### 10.1 `/dashboard` 总览

- 当前资产估值（若行情可用）、累计买入现金流、累计卖出回款、已实现收益、未实现收益。
- 活动仓位列表：证券、持股数、加权单位成本、最新价、盈亏额/盈亏率、最近目标、行情时间。
- “隐藏已结清”默认打开；关闭后显示完成的周期汇总。

### 10.2 `/security/{market}/{symbol}` 个股详情

- 股票名称、代码、最新价和数据新鲜度。
- 分时/K 线图（V1）叠加买卖点。
- **持仓批次表**：日期、初始/剩余股数、含费单位成本、已实现收益、浮动收益、目标价格、进度与状态。
- **卖出配对面板**：FIFO 推荐 + 可编辑配对数量 + 实时预览预计盈亏。
- **已结清折叠区**：按交易周期归档，展开显示配对关系。

### 10.3 `/trades` 交易记录

- 可筛选账户、证券、方向、时间和策略。
- 手动录入、修改原因说明、审计历史；V1 支持导入文件预览/提交/错误下载。

### 10.4 `/analytics` 交易复盘

- 按证券、策略、周期、月份查询已实现收益；亏损周期同样显示。
- 区分“现金流”、“已实现收益”、“未实现收益”和“累计回本参考价”。
- 不把回本参考价或单笔胜率包装为总体投资收益率。

### 10.5 交互原则

- **红涨绿跌**可配置（A 股习惯），盈利/亏损语义使用文案和符号辅助区分。
- 所有价格旁显示数据时间；无数据、价格过期、费用未录入须可见。
- 风险操作（撤销交易、重配）弹出确认，并告知受影响的配对与统计。
- 手机窄屏适配，但优先优化桌面浏览器批量记录与查看体验。

---

## 11. 业务校验与边界情况

1. `qty > 0`，`price > 0`；金额和费用不允许非法负数（退款/冲正必须用独立事件）。
2. 卖出必须完整匹配到当前账户、证券、策略且可用的历史 Lot；禁止超卖和跨账户误配。
3. 仅允许卖出时间不早于对应买入时间；普通 A 股应按照 T+1 等规则提示不合规配对，历史导入若与账单冲突需标错并人工复核。
4. 不把“买入必须整百股”写死到核心账务：零股处理、公司行动、特殊证券可能不同；交易规则使用可配置证券类型约束。
5. 同一日多笔成交必须使用可稳定排序的时间 + 来源成交序号/录入序号，FIFO 输出应可复现。
6. 买入批次被部分卖出后，更正其买入价格/费用必须触发所有相关配对成本重算。
7. 撤销已配对的买入需要先处理依赖它的卖出匹配；界面说明影响，默认拒绝静默级联删除。
8. 行情字段为空、停牌、除权除息、价格不连续时，明确展示原因，不默默采用错误价格计算目标。
9. 分红、送股、拆分等公司行动在 V1 支持前，遇到相关资产须提示“尚未纳入调整，成本/数量可能不准确”；不得假装历史交易已正确复权。
10. 成交存在多市场同名证券时，以代码及交易市场为准。

---

## 12. 非功能性需求

### 12.1 性能目标

- 自用规模：账户不超过 10 个、累计成交记录 100,000 条、自选股不超过 100 只（V1 设计容量）。
- 本地已缓存数据下，普通持仓聚合接口 p95 响应不超过 300ms（目标值，需基准测试验证）。
- 浏览器刷新不会触发行情源重复全量请求；多个标签页共享同一服务端采集缓存。
- 长期历史报表允许异步生成，但不得阻塞新成交记账。

### 12.2 安全与可靠性

- 内网部署可关闭登录，但必须绑定可信网络，不默认向公网暴露管理端点。
- 公网访问强制认证、HTTPS、限流及 CSRF/CORS 等适用防护。
- 所有涉及账本修改的接口审计记录不可缺失。
- SQLite 开启 WAL，周期性一致性检查，数据库备份与恢复演练。
- CSV 导入防公式注入，禁止文件路径穿越；服务器不执行用户上传内容。
- 应用日志默认不输出敏感账户数据，错误响应不泄漏数据库结构和堆栈。

### 12.3 可观测性

- 结构化日志：请求 ID、成交 ID、配对 ID、数据源、耗时、异常分类。
- `/health`：应用/数据库健康；`/ready`：服务就绪；行情源不可用不应导致账本 API 被判整体不可用。

---

## 13. 推荐技术选型和目录

| 层 | 建议实现 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Element Plus |
| 图表 | Apache ECharts；K 线也可选 Lightweight Charts |
| 后端 | Python 3.12+ + FastAPI + Pydantic v2 |
| 数据库 | SQLite + SQLAlchemy 2.x + Alembic |
| 定时采集 | `asyncio` 调度 / APScheduler；同步行情 SDK 置于线程池 |
| 数据处理 | `decimal.Decimal`；Pandas 仅用于批量导入、报告，不用于核心账务金额运算 |
| 行情 | 可插拔 QuoteProvider（AKShare 第一适配器） |
| 测试 | pytest + Hypothesis（配对不变量）+ Playwright（关键页面） |
| 部署 | Docker Compose；单后端进程起步，SQLite 数据目录持久化 |

```text
ashare-journal/
├── SPEC.md
├── backend/
│   ├── app/
│   │   ├── api/                 # routes / schemas
│   │   ├── domain/              # trade, lot, match, cycle, target
│   │   ├── services/            # matching, accounting, targets
│   │   ├── repositories/        # persistence
│   │   ├── market_data/         # providers, collector, cache
│   │   ├── models/              # SQLAlchemy
│   │   ├── db/                  # migrations, transaction handling
│   │   └── main.py
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── api/
│   │   └── stores/
│   └── package.json
├── compose.yaml
└── README.md
```

**服务端分层约束**：业务计算应是纯 Python 域服务（可独立单元测试），不得依赖 FastAPI 请求上下文；行情采集失败不得阻塞账务操作。

---

## 14. 测试与验收标准

### 14.1 单元测试

- 同一 Lot 全卖、部分卖、多笔卖，剩余股数与成本准确。
- 一笔卖出匹配多个 Lot；FIFO 与手动配对分别得到正确利润。
- 含交易费用的多笔配对，买入/卖出费用只分摊一次，尾差守恒。
- 多轮清仓重建仓，Cycle 划分准确。
- 所有资金流/持仓/配对不变量（见 5.7）成立；使用随机生成交易序列进行属性测试。
- 目标收益率净额/毛额、最低佣金、计划数量冲突的处理准确。

### 14.2 API 集成测试

- 重复 `client_request_id` 不重复创建交易。
- 两个并发卖出争用同一个 Lot 时，只能有满足余额的一笔或两笔合法组合成功。
- 撤销、修改有审计事件；数据库异常时交易和配对完全回滚。
- CSV 导入预览与提交分离；重复导入不会改变收益。
- 行情源不可用时手动录入、查询历史收益可继续工作。

### 14.3 E2E 测试

- 从空账本录入两次买入、一次卖出，验证例 6 的所有数值。
- 点击隐藏/显示已结清，验证内容变化但总收益不变化。
- 设置两档止盈计划，模拟有效报价到价、过期报价、重复报价，验证提醒次数与状态。
- 完全清仓后 Cycle 自动归档，再买入创建新 Cycle。

### 14.4 MVP 完成定义（Definition of Done）

- 用户能从空数据库完成：新增证券 → 买入两批 → 卖出指定批次 → 部分/全部抵消 → 隐藏已结清 → 查看收益 → 设置目标 → 查看价格。
- 所有验收案例自动化通过；账务使用 `Decimal`，无裸 `float` 金额计算。
- 数据库备份/恢复可验证；README 包含一键启动和数据目录说明。
- 断开互联网时，交易账本仍可查、可编辑；行情明确显示不可用或已过期。
- 不存在用户点击目标价提醒即触发真实交易的行为。

---

## 15. 迭代计划

**里程碑 M0：账本核心**

- 数据模型与迁移；买入/卖出 API；配对引擎；收益计算；不变量和测试。
- 交付物：即使没有前端也能通过 API 完成完整交易周期。

**里程碑 M1：可用 MVP**

- Vue 页面：总览、交易录入、批次表、已结清折叠、止盈计划。
- 自选股行情只读接口；数据过期与异常展示。

**里程碑 M2：导入与复盘**

- CSV/Excel 导入；交易周期、策略分组；K 线交易点；聚合报表。
- 分红/拆股/送股的准确处理及历史数据调整。

**里程碑 M3：增强与优化**

- WebSocket/SSE、告警、备份 UI；统计报表；PostgreSQL 可选迁移；行情供应商切换。

---

## 16. 明确不做的事

- **不自动交易、不批量下单、不绕过券商限制。**
- **不依赖券商账户接口**读取仓位；所有持仓由导入/手动成交生成。
- **不以实时行情作为交易事实源**；行情仅用于市值、浮盈和提醒。
- **不删除或自动抹掉已抵消交易**；仅折叠。
- **不把策略累计回本价当作证券真实持仓成本。**
- **不在缺少公司行动信息时宣称历史收益精准。**

---

## 17. 尚待确认的产品决策（有默认值，可直接启动实施）

| 问题 | 建议默认决策 |
|---|---|
| 买卖匹配模式 | 以用户手动指定为核心；默认给 FIFO 推荐，不静默更改用户的配对 |
| 存储 | SQLite 本地持久化；多用户/多设备并发规模扩大后考虑 PostgreSQL |
| 策略分仓 | 每笔成交归属一个策略，跨策略卖出通过逻辑拆单完成 |
| 手续费 | 默认用户逐笔录入真实金额；可选费用模型只用于预估 |
| 收益目标 | 按未结清 Lot 设置，支持毛收益率和扣预估卖出费后的净收益率 |
| 隐藏规则 | 默认隐藏已结清 Lot；历史流水永不隐藏，除非用户筛选 |
| 分红 | MVP 不主动计算；V1 纳入独立现金事件，明确提示未支持时数据可能偏差 |
| 登录 | 内网单用户 MVP 可不设；公网部署必须先加认证 |

**推荐首个编码任务**：从 M0 的 `TradeMatch` + `AccountingService` 开始，建立示例 6 及 5.7 的自动化测试。该部分是全部后续界面的可信基础。
