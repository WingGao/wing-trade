# M0 账本领域层与数据库 (m0-ledger-domain-db) — PRD

## Goal

实现 A 股个人交易系统的核心账本持久化模型（SQLite + SQLAlchemy 2.x + Alembic）与纯 Python 领域核算引擎（批次成本、FIFO/手动配对、尾差守恒、交易周期开闭、分段止盈目标价计算及审计记录），使得无需前端与 Web 层即可完成完整交易周期的精确核算与不变量验证。

## Confirmed Facts

- 规范依据：[docs/feat/ashare_personal_trading_spec.md](../../../../docs/feat/ashare_personal_trading_spec.md) §3, §4, §5, §6, §7, §11, §14.1。
- 所属父任务：[11-05-ashare-personal-trading](../11-05-ashare-personal-trading/prd.md) 的首个子任务（无前置任务依赖）。
- 精度要求：数量 `DECIMAL(20,4)`，价格 `DECIMAL(20,6)`，金额与费用 `DECIMAL(20,4)`；全部计算使用 `decimal.Decimal`（默认舍入策略 `ROUND_HALF_UP`），禁止任何二进制 `float` 参与账务运算。
- 数据库：SQLite（开启 `WAL` 与 `foreign_keys=ON`），通过自定义 SQLAlchemy TypeDecorator 将 `Decimal` 存为固定格式十进制字符串或精确类型，避免 SQLite `REAL` 浮点失真。

## Requirements

- **R-01 核心持久化模型与迁移**：
  - 建立 `accounts`、`securities`（`(market, symbol)` 唯一）、`watchlist_items`、`strategies`、`cycles`、`trades`（支持 `client_request_id` 幂等唯一键与 `revision`）、`lots`（`buy_trade_id` 唯一）、`trade_matches`（`(sell_trade_id, lot_id)` 唯一）、`profit_targets`、`quote_cache`、`audit_events` 表及 Alembic 初始迁移。
- **R-02 买入建批与现金流核算（`AccountingService`）**：
  - `buy_gross = round_4(buy_price * buy_qty)`，`buy_fees_total = commission + stamp_duty + transfer_fee + other_fee`，`buy_cash_out = buy_gross + buy_fees_total`。
  - 创建对应 `Lot`，`original_cost = buy_cash_out`，`unit_cost = round_6(original_cost / buy_qty)`。
- **R-03 卖出配对与尾差守恒引擎（`MatchingService`）**：
  - 支持 FIFO 默认预配对（按买入时间 `executed_at ASC, created_at ASC, id ASC` 稳定排序）与手动指定 `(lot_id, matched_qty)` 配对。
  - 校验规则：`qty > 0`、`price > 0`、费用非负；卖出配对总量严格等于 `sell_qty`；每个匹配 `Lot` 必须属于同一 `account_id + security_id + strategy_id`，`lot.executed_at <= sell.executed_at`，且 `matched_qty <= lot.remaining_qty`。
  - 买方成本分摊：对某 `Lot`，若本次匹配后 `lot.remaining_qty == 0`（最后一段），`allocated_buy_cost = lot.remaining_cost`；否则 `allocated_buy_cost = round_4(lot.original_cost * matched_qty / lot.original_qty)`。
  - 卖方净额分摊：`sell_cash_in = round_4(sell_price * sell_qty) - sell_fees_total`；同一笔卖出匹配多个 `Lot` 时，前 $k-1$ 段按比例四舍五入，最后一段取 `sell_cash_in - SUM(前 k-1 段 allocated_sell_net)`。
  - 已实现盈亏：`realized_pnl = allocated_sell_net - allocated_buy_cost`。
- **R-04 持仓、周期（`CycleService`）与回本参考价计算**：
  - 同一 `(account_id, security_id, strategy_id)` 从 0 股首次买入开启 `OPEN` Cycle；卖出后净持仓归 0 自动关闭 Cycle（`status='CLOSED'`）；再次买入开启新 Cycle。
  - 计算持仓聚合视图（支持 `include_closed: bool` 过滤，且过滤开关不影响任何累计已实现收益、未实现浮盈与周期收益）。
  - 计算未清仓 Cycle 的策略累计回本参考价 `strategy_breakeven_price = (SUM(buy_cash_out) - SUM(sell_cash_in)) / remaining_qty`；若已回收资金大于累计投入则标识“资金已全部回收，盈余 ¥X”，不覆盖会计持仓成本。
- **R-05 分段止盈目标核算（`TargetService`）**：
  - 支持为 `OPEN` Lot 配置多档 `ProfitTarget`（`target_return_pct`, `planned_sell_qty`, `calculation_basis` 为 `GROSS` 或 `NET`）。
  - 校验同一 Lot 下有效目标的 `SUM(planned_sell_qty) <= lot.remaining_qty`；当发生卖出导致 `remaining_qty < SUM(planned_sell_qty)` 时标记超量冲突状态。
  - 支持考虑最低佣金（如 5 元）、佣金率、印花税率、过户费率的非线性净收益目标价求解。
- **R-06 交易撤销与审计（Void & Audit）**：
  - 支持撤销 `SELL` 交易并原子回退对应 `Lot` 余额与 `Cycle` 状态；若撤销已被配对的 `BUY` 交易，抛出明确的领域异常 `LotHasActiveMatchesError`。所有状态变更写入 `audit_events`。

## Acceptance Criteria

- [ ] `alembic upgrade head` 与 `alembic downgrade base` 在 SQLite 上无报错执行。
- [ ] Spec §6 典型案例单元测试全部通过（Lot A 1000@10、Lot B 1000@9、S1 1000@11 全配 Lot A 时：Lot A `CLOSED`，Lot B 剩 1000 成本 9000，累计已实现收益 `1000.0000`，`strategy_breakeven_price = 8.000000`；改为 600+400 拆分、后续再卖 400、以及超卖 2100 拒绝录入均符合预期）。
- [ ] 使用 `Hypothesis` 编写随机交易序列属性测试，验证 Spec §5.7 的全部 6 条对账不变量在多笔分批买卖、含手续费、尾差分摊场景下 100% 成立。
- [ ] 净收益目标价求解器在含最低 5 元佣金阈值下反算所得净收益率误差 `< 0.01%`。
- [ ] 领域与服务层代码 `ruff check` 与 `mypy` / `pyright` 零错误，且不含任何 `float` 账务运算。

## Out of Scope

- FastAPI HTTP 路由与 Pydantic Web Schema（归属 `11-05-m0-ledger-api`）。
- 外部实时行情拉取（归属 `11-05-m1-market-quote`）。
- CSV 导入、分红送股公司行动（归属 V1）。
