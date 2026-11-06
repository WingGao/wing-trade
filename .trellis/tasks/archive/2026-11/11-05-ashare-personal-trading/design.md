# A 股个人交易管理与盯盘系统 (MVP) — Overall Technical Design

## 1. Architecture & Layer Boundaries

系统采用前后端分离的单体自托管架构（Docker Compose 部署），后端遵循领域驱动与整洁分层原则，确保账务核心完全独立于 Web 框架与外部行情源：

```text
wing-trading/
├── backend/
│   ├── app/
│   │   ├── domain/          # 纯 Python 领域实体、值对象、异常与计算规则（零 I/O、零框架依赖）
│   │   ├── services/        # 领域/应用服务：AccountingService, MatchingService, CycleService, TargetService, TradeLedgerService
│   │   ├── models/          # SQLAlchemy 2.x ORM 模型（SQLite WAL 持久化）
│   │   ├── repositories/    # 数据访问与工作单元（事务锁、乐观锁校验）
│   │   ├── market_data/     # QuoteProvider 接口、AKShare 适配器、行情缓存与采集调度器
│   │   ├── api/             # FastAPI 路由、Pydantic v2 请求/响应契约、统一错误处理器
│   │   ├── db/              # 数据库连接、Session 管理、Alembic 迁移配置
│   │   └── main.py          # FastAPI 应用入口与生命周期管理
│   ├── alembic/             # 数据库迁移脚本
│   ├── tests/               # unit (含 Hypothesis 属性测试) + integration
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/             # 类型化 REST 客户端
│   │   ├── stores/          # Pinia 状态管理（账户、行情刷新、UI 偏好如隐藏已结清/红涨绿跌）
│   │   ├── pages/           # /dashboard, /security/:market/:symbol, /trades, /analytics
│   │   └── components/      # LotTable, SellMatchDrawer, ProfitTargetModal, QuoteBadge 等
│   └── package.json
└── compose.yaml
```

### Layer Dependency Rules
- `domain/` 仅依赖 Python 标准库（`decimal`, `datetime`, ` dataclasses`, `enum`, `uuid`）。
- `services/` 编排领域计算与仓储接口，不在服务层直接引用 FastAPI `Request` 或 `HTTPException`。
- `market_data/` 单向被 `api/` 或只读估值服务调用；`market_data/` 的任何异常必须被捕获并转化为带 `is_stale=True` 的降级行情对象，严禁向上传播阻断账务接口。

## 2. Core Data Flow & Contracts

### 2.1 买入建批流（Buy -> Lot -> Cycle）
1. 客户端提交 `POST /api/v1/trades/buy`（含 `client_request_id`）。
2. 服务端开启 `BEGIN IMMEDIATE` 事务，检查 `(account_id, client_request_id)` 幂等性。
3. 查找 `(account_id, security_id, strategy_id)` 下是否存在 `OPEN` 状态的 `Cycle`；若无（当前净持仓为 0），新建 `Cycle(status='OPEN')`。
4. 计算 `gross_amount = round(price * qty, 4)`，`fees_total = commission + stamp_duty + transfer_fee + other_fee`，`original_cost = gross_amount + fees_total`。
5. 插入 `Trade(side='BUY')`、`Lot(original_qty=qty, original_cost=original_cost, remaining_qty=qty, remaining_cost=original_cost, status='OPEN')` 及 `AuditEvent`。

### 2.2 卖出配对与尾差守恒流（Sell -> TradeMatch -> Lot/Cycle Update）
1. 预览阶段（`POST /api/v1/trades/sell/preview`）：读取该账户、证券、策略下所有 `OPEN` 且 `executed_at <= sell.executed_at` 的 `Lot`，按 `(buy_executed_at ASC, buy_trade_created_at ASC, id ASC)` 稳定排序，生成默认 FIFO 配对方案或校验用户自定义配对，返回预计各批次分摊成本、分摊净收入与预计已实现盈亏。
2. 提交阶段（`POST /api/v1/trades/sell`）：
   - 单写事务内锁定目标 `Lot` 记录，校验 `SUM(matches.qty) == sell.qty`，且每个 `Lot` 的 `matched_qty <= lot.remaining_qty`。
   - **买方成本分摊（Lot 维度尾差守恒）**：若某笔匹配使得该 `Lot` 的累计已配对数量达到 `lot.original_qty`（即该 Lot 最后一段配对），则 `allocated_buy_cost = lot.remaining_cost`；否则 `allocated_buy_cost = round_4(lot.original_cost * matched_qty / lot.original_qty)`。
   - **卖方净额分摊（Sell Trade 维度尾差守恒）**：设 `sell_cash_in = sell_gross - sell_fees_total`。对同一笔卖出下的多段 `TradeMatch`，前 $k-1$ 段按 `round_4(sell_cash_in * matched_qty / sell_qty)` 计算 `allocated_sell_net`，最后一段（第 $k$ 段）取 `sell_cash_in - SUM(前 k-1 段 allocated_sell_net)`，确保 `SUM(allocated_sell_net) == sell_cash_in`。
   - 更新各 `Lot` 的 `remaining_qty`、`remaining_cost` 与 `status`（归零则置 `CLOSED`）；同时检查各 `Lot` 下关联的 `ProfitTarget`，若剩余股数小于未完成计划卖出量则标记冲突提示。
   - 若当前 `(account_id, security_id, strategy_id)` 下所有 `Lot` 的 `remaining_qty` 之和归零，将当前 `Cycle` 置为 `CLOSED` 并记录 `closed_at`。

### 2.3 交易撤销与重算流（Void Trade）
- 撤销卖出交易（`SELL`）：事务内将该 `Trade` 标记为 `VOIDED`，软删除/归档其关联的 `TradeMatch`，按原分摊值恢复对应 `Lot` 的 `remaining_qty`、`remaining_cost` 及 `OPEN` 状态，必要时重开或重算受影响的 `Cycle`，写入 `AuditEvent`。
- 撤销买入交易（`BUY`）：若对应 `Lot` 存在任何有效 `TradeMatch`，默认拒绝静默级联删除并返回 `LOT_HAS_ACTIVE_MATCHES` 错误及受影响卖出列表，要求用户先撤销或重配相关卖出成交。

## 3. Compatibility, Migration & Operational Notes

- **SQLite 到 PostgreSQL 演进**：所有金额字段在 ORM 层使用自定义 `DecimalText` 或 `Numeric(20, 4)` / `Numeric(20, 6)` 映射，避免 SQLite 默认将 `Numeric` 转为二进制 `REAL` 浮点数导致精度丢失。
- **备份与恢复**：SQLite 数据文件固定挂载于 `./data/ashare_journal.db`，开启 `PRAGMA journal_mode=WAL;` 与 `PRAGMA foreign_keys=ON;`。
