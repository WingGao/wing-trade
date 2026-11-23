# Quality Guidelines

## Overview

Backend accounting code is Decimal-only, deterministic, and test-first at the
domain boundary. Run `ruff check app tests`, `mypy app`, and `pytest -v` when the
development dependencies are available.

## Forbidden Patterns

- Do not accept binary `float`, `NaN`, or infinity in accounting inputs.
- Do not use SQLAlchemy `Float`/`REAL` for quantities, prices, fees, or P&L.
- Do not duplicate fees in both sell net and realized P&L.
- Do not silently return a negative breakeven price; expose recovered surplus
  separately.
- Do not mutate a lot or cycle without an `audit_events` row in the same
  transaction.

## Required Patterns

- Normalize quantity, price, and amount through `round_qty`, `round_price`, and
  `round_amount` before arithmetic or persistence.
- Keep pure matching/accounting functions independent from SQLAlchemy where
  possible; keep transaction ownership in `TradeLedgerService`.
- Preserve tail rounding by assigning the final sell-net residue to the last
  match.
- Prefer focused domain errors carrying a stable `code` and optional `details`.

## Testing Requirements

- Every accounting rule needs a deterministic unit test and boundary/error case.
- Randomized allocation tests must assert conservation, not implementation
  details.
- Integration tests cover ORM constraints, migrations, idempotency, voiding, and
  audit events.
- If a dependency is unavailable, report the exact skipped command; do not claim
  it passed.
- 本地与受限环境下使用 `uv run` 运行后端检查/测试时，统一携带 `--cache-dir .uv-cache --offline --no-sync --project backend`，避免访问全局 `uv` 缓存目录导致权限拒绝或触发联网。
- Windows 沙箱环境会阻塞 Python `asyncio.run(...)` 及 FastAPI `TestClient` 所需的本地 loopback socketpair，导致异步单测（如 `test_security_info.py`）和集成测试挂起；纯同步单测可在沙箱内直接运行，全量 `pytest backend/tests` 需在非沙箱环境下执行。

## Code Review Checklist

- Decimal scales and rounding mode are explicit.
- No binary floating point enters a ledger calculation.
- FIFO ordering is stable and manual match scope is validated.
- Lot/Cycle/Trade state changes and voids are auditable.
- Migration upgrade and downgrade are reversible.
- Tests cover both normal and rejection paths.

## Scenario: 自动卖出配对与预览确认

### 1. Scope / Trigger

- 触发条件：新增或修改卖出自动配对策略、卖出预览 DTO，或预览到正式卖出的批次一致性。
- 自动配对属于账务边界，不能由前端重新实现；预览与确认必须复用同一匹配和分摊规则。

### 2. Signatures

- `MatchingService.auto_preview(lots, sell_qty, *, method=FIFO, sell_executed_at=None, account_id=None, security_id=None, strategy_id=None) -> list[MatchRequest]`
- `POST /api/v1/trades/sell/preview`，请求可选 `matchingMethod`，响应包含 `currency`、`executedAt` 和逐批匹配明细。
- `POST /api/v1/trades/sell`，请求可直接传 `matchingMethod`，也可传预览产生的明确 `matches`。

### 3. Contracts

- 自动策略接受 `FIFO`、`LIFO`、`EQUAL`；省略策略时保持 `FIFO`。传入明确批次时按 `MANUAL` 处理。
- `FIFO` 按 `(executed_at, created_at, id)` 升序，`LIFO` 与 `EQUAL` 按同一键降序。EQUAL 在最近优先最小覆盖集合内按容量均分，整数余股优先较新批次，详见持仓批次契约。
- 自动候选必须为 `OPEN`、剩余数量大于零、范围和策略一致，且买入时间不晚于卖出时间。
- 预览匹配至少返回 `lotId`、`buyExecutedAt`、`buyPrice`、`buyQty`、`availableQty` 和 `matchedQty`；价格与数量保持十进制字符串。
- `buyPrice` 使用买入交易原始成交价，不能从含费 `unitCost` 反推；`allocatedBuyCost` 继续使用包含费用分摊的服务端结果。
- 工作台确认必须提交预览中的明确 `lotId` 和 `matchedQty`，不得在确认时重新自动选择批次。

### 4. Validation & Error Matrix

| 条件 | 必须结果 |
|---|---|
| 自动策略为 `MANUAL` 或非法值 | `VALIDATION_ERROR` 或请求校验错误 |
| 可用批次不足 | `INSUFFICIENT_LOT_QTY`，并包含请求量和缺口 |
| 明确批次数量缺失 | `VALIDATION_ERROR` |
| 明确批次跨账户、证券或策略 | `LOT_OWNERSHIP_MISMATCH` |
| 明确批次买入晚于卖出 | `FUTURE_LOT` |

### 5. Good / Base / Bad Cases

- Good：较新批次 4 股、较旧批次 10 股，卖出 6 股时 EQUAL 按 `3 + 3` 分配；明确 LIFO 仍为 `4 + 2`，两者均保持总量守恒。
- Base：旧调用方省略策略，仍由最早买入批次开始分配。
- Bad：预览返回 LIFO，正式卖出却省略明确批次并按不同状态重新选择。

### 6. Tests Required

- 单测覆盖 EQUAL 等量/容量/余股、LIFO 跨批次、同时间确定性、范围过滤、未来批次、零数量、已结清和 FIFO 默认兼容。
- API 集成测试逐字段断言预览与正式卖出的 `matchedQty`、`allocatedBuyCost`、`allocatedSellNet`、`realizedPnl` 一致。
- 手续费场景断言原始买入价与含费分摊成本不同时，两者分别正确返回。

### 7. Wrong vs Correct

#### Wrong

```python
requests = MatchingService.auto_preview(lots, qty, method=MatchingMethod.LIFO)
# 确认时省略 matches，后端可能依据变化后的持仓重新选择批次。
```

#### Correct

```python
preview = api.preview_sell(..., matching_method=MatchingMethod.LIFO)
api.sell(..., matches=[MatchRequest(m.lot_id, m.matched_qty) for m in preview.matches])
```

## Scenario: 结构化 YAML 配置优先与环境变量回退

### 1. Scope / Trigger

- 触发条件：新增、修改应用配置项（`backend/app/config.py`）、YAML 模板（`config.example.yaml`）或容器环境变量透传（`compose.yaml`）。
- 属于基础设施配置边界，必须在单点维护白名单 schema、优先级与校验规则。

### 2. Signatures

- `Settings.load(config_file: str | os.PathLike[str] | None = None, *, discover_default: bool = True) -> Settings`
- `Settings.from_env() -> Settings`（兼容入口，内部委托 `Settings.load()`）
- 模块级实例：`settings = Settings.load()`

### 3. Contracts

- **加载优先级（字段级）**：`YAML 配置 > 环境变量（env）> 内置默认值`。
- **路径查找顺序**：显式 `config_file` 参数 > 环境变量 `CONFIG_FILE` > 当前工作目录 `config.yaml` / `config.yml` > 项目根目录 `config.yaml` / `config.yml` > 无文件（回退纯 env 与默认值）。
- **结构化分组**：仅允许顶层分组 `database`（含 `mysql`）、`logging`、`quote`、`auth`、`email`（含 `smtp`）、`frontend`，受 `_YAML_SCHEMA` 白名单约束。
- **空值与假值**：省略或 `null` 回退 env；布尔 `false` 与数字 `0` 视为显式值不回退 env；密码字段（`database.mysql.password`、`email.smtp.password`）禁止 `strip()`。
- **测试隔离**：`backend/tests/conftest.py` 默认隔离 `CONFIG_FILE` 与 `_default_config_candidates`，防止开发者本地 `config.yaml` 污染单元测试。

### 4. Validation & Error Matrix

| 条件 | 必须结果 |
|---|---|
| 显式 `config_file` 或 `CONFIG_FILE` 不存在 | 抛出 `ValueError`（含路径），禁止静默回退 |
| YAML 语法错误或根节点非 mapping | 抛出 `ValueError` |
| YAML 包含未知分组或未知键 | 抛出 `ValueError("Unknown config key: <path>")` |
| 布尔值传入端口或正整数（如 `port: true`） | 抛出 `ValueError`（显式拦截 `isinstance(raw, bool)`） |
| MySQL / SMTP 必填项缺失或 TLS/SSL 同时为 true | 抛出 `ValueError` |

### 5. Good / Base / Bad Cases

- Good：YAML 仅覆盖 `database.mysql.host` 与 `auth.cookie_secure: false`，其余项回退 env，且 `cookie_secure` 保持 `False`。
- Base：无 YAML 文件且未设 `CONFIG_FILE` 时，完全保持既有纯环境变量行为。
- Bad：使用 `yaml_val or os.getenv(...)` 导致 `false` 被环境变量覆盖，或把 `config.yaml` 静默忽略掉未知拼写键。

### 6. Tests Required

- 单元测试覆盖：完整结构化 YAML 覆盖 env（含假值）、部分 YAML + `null` 回退、默认发现顺序（`config.yaml` > `config.yml`）、空 YAML 文件、异常拦截（缺失/语法/非 mapping/未知键/布尔误传整数），以及直接加载根目录 `config.example.yaml` 校验模板不漂移。

### 7. Wrong vs Correct

#### Wrong

```python
# 错误：truthiness 判断会导致 YAML 的 false 回退到环境变量
cookie_secure = yaml_config.get("auth", {}).get("cookie_secure") or os.getenv("AUTH_COOKIE_SECURE")
```

#### Correct

```python
# 正确：仅在 YAML 缺失或为 None 时回退环境变量
cookie_secure = _resolve_bool(yaml_config, ("auth", "cookie_secure"), "AUTH_COOKIE_SECURE", default=False)
```
