# Database Guidelines

## Scenario: M0 Ledger Domain and Database

### 1. Scope / Trigger

This contract applies to the SQLite ledger schema, SQLAlchemy ORM models, Alembic
migrations, and services that mutate `Trade`, `Lot`, `TradeMatch`, or `Cycle`.
The accounting boundary must preserve Decimal values exactly and every fact/state
change must remain auditable.

### 2. Signatures

- `ExactDecimal(scale: int)` stores fixed-scale text and returns `Decimal`.
- `TradeLedgerService.buy(..., qty, price, fees, client_request_id, commit=True) -> Trade`.
- `TradeLedgerService.sell(..., qty, price, matches=None, fees, client_request_id, commit=True) -> Trade`.
- `TradeLedgerService.void_trade(trade_id, reason=None, commit=True) -> Trade`.
- `MatchingService.allocate(lots_by_id, requests, sell_qty, sell_cash_in, ...) -> list[MatchAllocation]`.

### 3. Contracts

- Tables use lower snake case. IDs are UUID strings. Decimal columns use
  `ExactDecimal(4)` for quantity/amount and `ExactDecimal(6)` for price/rates.
- `trades.client_request_id` is unique per account for idempotent submissions.
- Buy cash out is `round_4(price * qty) + fees`; sell cash in is
  `round_4(price * qty) - fees`.
- Automatic matching is deterministic by `(executed_at, created_at, id)`: FIFO
  uses ascending order, LIFO uses descending order, and an omitted strategy
  remains FIFO. Automatic candidates must be OPEN, have positive remaining
  quantity, match the sell scope, and be bought no later than the sell.
- `EQUAL` 沿用最近优先排序，在能覆盖卖出的最小集合内按容量均分，整数余股按最近优先；旧小数持仓保持四位精度与数量守恒。界面默认显式传 EQUAL，未指定策略的旧 API 仍默认 FIFO。
- Manual matches must total the sell quantity and belong to the same
  account/security/strategy.
- A final match consumes the lot's current remaining cost; partial matches use
  `round_4(original_cost * matched_qty / original_qty)`. Sell-net tail rounding
  goes to the final match.
- SQLite connections enable `foreign_keys=ON` and `journal_mode=WAL`.
- State changes emit `audit_events` for `TRADE`, `LOT`, and `CYCLE` entities in the
  same session transaction.
- `DATABASE_URL` and `ECHO_SQL` are optional environment keys; defaults are
  `sqlite:///./ledger.db` and disabled SQL echo.

### 4. Validation & Error Matrix

| Condition | Required result |
|---|---|
| Quantity or price is non-positive | `InvalidQuantityError` / `InvalidPriceError` or `ValidationError` |
| Fee is negative, NaN, or infinite | `NegativeFeeError` / `TypeError` / `ValueError` |
| Manual match quantity differs from sell quantity | `MatchQuantityMismatchError` |
| Lot has insufficient quantity | `InsufficientLotQtyError` |
| Lot scope differs from sell scope | `LotOwnershipError` |
| Lot was bought after the sell | `FutureLotError` |
| Buy with active sell matches is voided | `LotHasActiveMatchesError` |
| Duplicate account/client request | Return the existing trade without adding rows |

### 5. Good / Base / Bad Cases

- Good: buy 1,000 at 10, then sell 1,000 at 11; the lot closes and realized
  P&L is 1,000 before fees.
- Base: sell across two lots; the final allocated sell-net absorbs all rounding
  residue and total allocated net equals sell cash in exactly.
- Bad: query SQLite text-backed `remaining_qty` with SQL `> 0`; load Decimal
  values and filter in Python instead.

### 6. Tests Required

- Alembic `upgrade head` and `downgrade base` on SQLite.
- Unit tests for precision rejection, FIFO/manual validation, tail allocation,
  target price solving, and the documented Section 6 cases.
- Hypothesis tests assert match quantity conservation, allocated sell-net
  conservation, and lot cost plus remaining cost conservation.
- Integration tests assert buy/sell/void state, idempotency, and `AuditEvent`
  rows for Trade/Lot/Cycle changes.

### 7. Wrong vs Correct

#### Wrong

```python
session.scalars(select(Lot).where(Lot.remaining_qty > 0))
```

#### Correct

```python
lots = list(session.scalars(select(Lot).where(Lot.status == "OPEN")))
lots = [lot for lot in lots if lot.remaining_qty > 0]
```

SQLite stores `ExactDecimal` as text, so numeric filtering belongs in Python.

### Common Mistakes

- SQLite `DateTime(timezone=True)` values may be returned without timezone
  information. Normalize persisted and request timestamps to UTC before
  chronology comparisons.
- A slotted dataclass exposes field descriptors on the class. Read configuration
  defaults from an instance (for example `defaults = Settings()`) rather than
  from `Settings.database_url`.

## Naming and Migration Rules

- Use singular model names and plural table names (`Trade` / `trades`).
- Add schema changes through a new Alembic revision; never edit an applied
  revision in place.
- Keep unique constraints and indexes named explicitly (`uq_*`, `ix_*`).

## Scenario: MySQL 后端配置与启动迁移

### 1. Scope / Trigger

- 触发条件：修改数据库环境变量、SQLAlchemy 引擎参数、Compose 数据库编排或应用启动建表逻辑。
- 该契约覆盖 SQLite 与 MySQL 双后端，不允许切换后端时删除、清空或自动迁移现有账务数据。

### 2. Signatures

- `Settings.from_env() -> Settings`：解析 `DATABASE_URL` 或 `DATABASE_BACKEND=sqlite|mysql`。
- `create_engine_for_url(database_url=None, *, echo=None) -> Engine`：启用连接探活，并为 MySQL 设置连接回收。
- `prepare_database_schema(engine) -> None`：空库升级到 Alembic `head`，旧 `create_all` 库补齐表后标记当前版本。
- Docker Compose 使用 `COMPOSE_PROFILES=mysql` 启用 MySQL 服务。

### 3. Contracts

- `DATABASE_URL` 非空时优先级最高，覆盖结构化数据库配置。
- MySQL 分项变量为 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`，全部必需。
- MySQL URL 必须通过 `URL.create` 生成，驱动为 `mysql+pymysql`，字符集为 `utf8mb4`；密码不得要求手工 URL 编码。
- MySQL 引擎必须启用 `pool_pre_ping=True` 和 `pool_recycle=1800`；SQLite 继续启用 `check_same_thread=False` 与 PRAGMA。
- 启动时迁移必须在应用接收请求前完成；已有 `alembic_version` 的库只执行 `upgrade head`。
- 旧 SQLite 库若已有业务表但没有 `alembic_version`，允许先 `create_all` 补齐缺失表并 `stamp head`，禁止删除或重写业务行。
- 未启用 MySQL profile 时，Compose 只启动后端和前端，不得等待 MySQL。

### 4. Validation & Error Matrix

| 条件 | 必须结果 |
|---|---|
| `DATABASE_BACKEND` 不是 `sqlite` 或 `mysql` | 启动前抛出配置错误 |
| MySQL 必需变量缺失 | 启动前指出缺失变量 |
| `MYSQL_PORT` 非整数或超出 1..65535 | 启动前拒绝配置 |
| 空 MySQL 数据库 | 执行 Alembic 迁移并写入版本表 |
| 已版本化数据库 | 仅执行未应用 revision |
| 旧无版本账务库 | 保留行数据，补齐表并标记当前版本 |
| `COMPOSE_PROFILES` 未启用 `mysql` | Compose 不创建或等待 MySQL 服务 |

### 5. Good/Base/Bad Cases

- Good：`.env` 配置 `COMPOSE_PROFILES=mysql` 和 `MYSQL_*`，执行 `docker compose up --build` 后自动完成建表和启动。
- Base：不配置数据库变量时继续使用 SQLite，Compose 不启动 MySQL。
- Bad：把包含 `@`、`:`、`/` 的密码直接拼进 URL，导致连接串被错误解析。
- Bad：应用先 `create_all`，随后再执行初始 Alembic revision，造成重复建表。

### 6. Tests Required

- 配置测试断言显式 URL 优先、特殊字符密码按 URL 规则转义、非法后端和端口被拒绝。
- 启动迁移测试断言空 SQLite 创建版本表和核心表，旧无版本库升级后数据行仍存在且 `PRAGMA integrity_check=ok`。
- 引擎测试断言 MySQL `pool_pre_ping`、连接回收时间和 PyMySQL 驱动。
- Compose 测试断言 MySQL profile 开启时包含 `mysql` 服务，关闭时只包含 `backend` 与 `frontend`。
- 真实 MySQL 冒烟至少验证 `alembic upgrade head`、应用启动和 `/ready`。

### 7. Wrong vs Correct

#### Wrong

```python
url = f"mysql+pymysql://{user}:{password}@{host}/{database}"
```

#### Correct

```python
url = URL.create(
    drivername="mysql+pymysql",
    username=user,
    password=password,
    host=host,
    database=database,
    query={"charset": "utf8mb4"},
).render_as_string(hide_password=False)
```
