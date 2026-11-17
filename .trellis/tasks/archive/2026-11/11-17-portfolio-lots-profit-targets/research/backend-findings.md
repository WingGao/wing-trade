# 研究：后端账务重建、分级止盈与行情边界

- 查询：个股批次重建的事务和外键约束、三级止盈最小改动、等量卖出解释、名称及日内行情能力。
- 范围：仓库内部代码与既有任务；不访问或修改本地 `ledger.db`，不执行迁移或账务重建。
- 日期：2026-11-17。

## 结论

### 一、交易事实和派生重建

- 当前不存在重算接口。`TradeLedgerService.buy`（`backend/app/services/ledger.py:122`）创建交易和批次，`sell`（`:202`）创建交易和匹配；直接调用两者重放会重复交易，不能用于保留事实的重算。
- `Trade` 含原始数量、成交价、费用、状态、幂等键和导入信息（`backend/app/models/ledger.py:89`）；批次以唯一 `buy_trade_id` 关联买入（`:134`），它是重建的稳定锚点。
- 外键：`TradeMatch.lot_id=RESTRICT`（`:169`），`ProfitTarget.lot_id=CASCADE`（`:186`），`Lot.cycle_id=CASCADE`（`:143`），`Trade.cycle_id=SET NULL`（`:100`）。先匹配和目标，再批次，最后周期，不能停用外键或先提交删除。
- 建议独立重建服务，严格限定账户、证券、策略，复用 `begin_write` 的 `BEGIN IMMEDIATE`（`backend/app/api/utils.py:13`），全流程一个事务，异常全部 rollback。
- 重建前快照旧批次到买入交易的映射、明确目标和匹配。按标准化 UTC 的 `(executed_at, created_at, id)` 重放有效交易，同一成交时间保证对应买入先于卖出。复用 `AccountingService` 与 `MatchingService.allocate` 构造派生投影，原 `Trade.id`、金额、状态、幂等键及来源不变。
- 如果重建周期，先解除范围内交易的 cycle 关联再删旧周期，按累计剩余数量归零划分新周期。已作废交易不参与成本，但事实与审计必须保留。
- 推荐复用原 lot id；否则以 `buy_trade_id` 恢复显式目标，不能静默丢失批次设置。审计无实体外键（`backend/app/models/ledger.py:212`），保留旧事件并追加重建前后汇总及批次/周期变化事件，包含 actor 和原因。
- 保留旧明确匹配是最保守方案；若重算要应用新自动算法，须在设计明确。特别注意：当前工作台把自动预览以明确 matches 提交，持久化 `matching_method` 最终为 `MANUAL`（`backend/tests/integration/test_api_v1.py:205`），数据库无法区分真实用户手选和“自动预览后确认”。

### 二、三级止盈最小契约

- 目前仅有一批多目标 `ProfitTarget`（`backend/app/models/ledger.py:181`），字段为收益率、计划数量、NET/GROSS、估算价格、提醒开关及状态；无全局或个股默认。
- 现有路由 `GET/POST /lots/{lot_id}/targets`、`PATCH/DELETE /targets/{target_id}` 在 `backend/app/api/v1/router.py:546`、`:554`、`:575`、`:601`；DELETE 仅置 CANCELLED（`:607`）。应保持兼容。
- 新增独立默认设置表合理：GLOBAL 唯一作用域键，SECURITY 唯一证券键。若按账户区分默认，则账户维度要贯通所有读写；不要混合全应用和账户级语义。
- 默认保存 `targetReturnPct`、`calculationBasis`、`alertEnabled`，不要求跨批次通用的具体股数或绝对价格。沿用小数率单位，0.1 表示 10%，不要与 `AccountingService.return_pct` 输出的百分数混用。
- 建议提供默认读、写、清除接口；批次 DTO 新增纯读 `effectiveTarget`，字段至少含 `source: LOT|SECURITY|GLOBAL|NONE`、收益率、口径、估算价。有效显式批次目标 > 个股 > 全局。GET 和继承均不能创建 `ProfitTarget` 行。
- 继承估价复用 `TargetService.solve_price`（`backend/app/services/target.py:29`），数量动态取剩余量；CANCELLED 不能阻止继承，已结清批次不应产生可操作目标。
- 现代码陷阱：新增/更新计划量把 CANCELLED 目标也计入总量（`backend/app/api/v1/router.py:560`、`:584`），可能导致取消后再次设置失败，应过滤有效目标，编辑避免无限追加冲突目标。
- 当前行情提醒只评估持久化 lot.targets（`backend/app/market_data/service.py:151`）。用户强调继承“只是显示效果，数据库并不保存”，可将继承限定为只读展示；若要动态继承提醒必须单独设计状态，不能复制默认目标到批次。
- 新增 Alembic 版本，不修改已应用的初始迁移。

### 三、卖出“相同数量”的解释

- 上次任务约定 LIFO：新 4 股、旧 10 股、卖 6 股 → 4+2；省略策略继续 FIFO。实现在 `backend/app/services/matching.py:83`，候选过滤在 `:50`。
- 本次措辞可指“每批扣同样股数”，也可指买卖总量相同；后者现已保证。主任务应明确选择前者时的产品假设。
- 保守建议：保留 LIFO 排序，先选最少且足够覆盖的批次集合，再在选中集合内按整数股受容量约束均分；不足均分额的批次吃满，剩余继续分配，整数余数按 LIFO。例：4/10 卖 6 → 3+3，卖 10 → 4+6；三批各剩 4 股卖 10 → 4+3+3。这样小额卖出不触及所有久远批次。
- 建议新增明确策略（例如 EQUAL），保留 FIFO/LIFO 语义。工作台预览及确认使用同一组明确 matchedQty；不足、未来买入、跨账户证券策略继续原错误。
- “股数整数显示”不能默默授权清理数据库四位精度数量。若新策略只接受整数，要明确校验；若保留小数 API，需要记录兼容处理。
- 分摊继续使用 `MatchingService.allocate`（`backend/app/services/matching.py:240`）：成本按原始成本和数量比例，卖出净额按数量比例，最后一条吸收金额尾差，差价为 realizedPnl；不能改写剩余成本来吞掉盈亏或重复扣费。
- `list_positions`（`backend/app/api/v1/router.py:497`）已汇总所有有效卖出匹配，但详情 `list_lots` 仅汇总 CLOSED 批次（`:522`），部分卖出但仍持有的价差会漏计，应改为该范围所有有效匹配。

### 四、名称及真实日内曲线

- `list_securities` 只查本地数据库（`backend/app/api/v1/router.py:205`），`create_security` 使用请求名称（`:217`），无外部名称查询。
- 东财请求字段已有 f14（`backend/app/market_data/provider.py:222`），但 `QuoteSnapshot`（`:31`）无名称，`_snapshot_from_row`（`:343`）丢弃它。可增加资料查询复用 `_request_batch`（`:309`），严格验证市场代码，返回上游真实名称；网络失败不能清空原名称。
- `QuoteProvider` 仅 `get_quotes/health`（`:81`），`QuoteCache` 每证券一行（`backend/app/models/ledger.py:199`），没有分钟序列、日内历史表或分时端点。须新增真实上游分时方法和路由，不能把打开页面后的采样冒充当日历史。
- 可复用可注入传输、超时、响应上限、Decimal JSON 解析与非有限数字拒绝（`backend/app/market_data/provider.py:225` 至 `:340`）。失败保留旧曲线并标过期，行情失败不能禁止账务操作。
- 分时字段和日期需实现阶段通过原始上游响应确认。休市返回上个交易日时应显示真实日期，不能标为今天。本研究未请求公网，不宣称验证未实现接口。

## 文件清单与规范

| 文件 | 作用 |
|---|---|
| `backend/app/models/ledger.py` | 全部账务 ORM 与外键 |
| `backend/app/services/ledger.py` | 交易、作废、幂等、审计 |
| `backend/app/services/matching.py` | 候选、配对和分摊 |
| `backend/app/services/accounting.py` | 成本、浮盈、收益率 |
| `backend/app/services/cycle.py` | 周期与现金流 |
| `backend/app/services/target.py` | 目标价与数量校验 |
| `backend/app/api/v1/router.py` | 当前 HTTP 边界 |
| `backend/app/api/utils.py` | 写锁事务和回滚 |
| `backend/app/market_data/provider.py` | 可注入东财单点提供器 |
| `backend/app/market_data/service.py` | 缓存与显式目标提醒 |

- 规范：`.trellis/spec/backend/database-guidelines.md`、`quality-guidelines.md`、`market-data-guidelines.md`。
- 基线：`.trellis/tasks/archive/2026-11/11-17-sell-match-latest-buy/{prd,design}.md`。

## 验证建议

1. 临时库重建集成：多周期、多策略、账户隔离；重复重建一致；原 Trade 数量、ID、费用、幂等键、状态不变；作废交易排除；中途缺量后完整回滚；外键检查通过；显式目标和审计保留。
2. 匹配单测及属性测试：等量、整数余数、容量不足、全卖、缺量、同时间稳定顺序、FIFO/LIFO 兼容；数量、成本和卖出净额守恒。
3. 部分卖出后批次仍 OPEN，个股已实现盈亏包含差价；作废后汇总不重复计入。
4. 默认覆盖与清除恢复；GET 前后目标表行数不变；全局修改立即影响继承；CANCELLED 不占计划量；跨证券隔离。
5. 注入传输：正确名称、错误身份、空值、超时/响应超限；日内日期、排序、休市和失败保留，不依赖公网。
6. 后端 ruff、mypy、pytest，迁移只在临时数据库升降级；禁止对本地 ledger.db 跑破坏性测试。

## 局限与未找到项

- 无 `.codegraph/`，未调用 CodeGraph。未执行测试、迁移或本地数据库操作。
- 等量选批范围、余数规则、重算是否改历史匹配需主任务明确；本文件是保守技术建议。
