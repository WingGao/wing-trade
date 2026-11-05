# M0 账本领域层与数据库 (m0-ledger-domain-db) — Implementation Plan

## 1. Ordered Checklist

- [ ] **Step 1: 初始化后端工程与依赖 (`backend/pyproject.toml`)**
  - 配置 Python 3.12+ 依赖：`sqlalchemy>=2.0`, `alembic`, `pydantic>=2.0`, `pydantic-settings`, `pytest`, `hypothesis`, `ruff`, `mypy`。
- [ ] **Step 2: 实现领域基础类型、枚举、异常与精度工具 (`backend/app/domain/`)**
  - 实现 `precision.py`（`round_qty`, `round_price`, `round_amount`, `ensure_decimal`）、`enums.py`、`exceptions.py`、`models.py`。
- [ ] **Step 3: 实现 SQLAlchemy 2.x 模型与 Alembic 迁移 (`backend/app/db/`, `backend/app/models/`)**
  - 实现 `ExactDecimal` 类型装饰器、SQLite WAL 连接事件、全部 11 张表 ORM 定义及 `0001_initial_ledger_schema.py` 迁移脚本。
- [ ] **Step 4: 实现纯 Python 核算、配对、周期与止盈服务 (`backend/app/services/`)**
  - 实现 `AccountingService`、`MatchingService`、`CycleService`、`TargetService` 以及带事务和审计记录的 `TradeLedgerService`。
- [ ] **Step 5: 编写单元测试、属性测试与数据库集成测试 (`backend/tests/`)**
  - `test_spec_section6_cases.py`：完整覆盖 Spec §6 的基准与进阶验收案例。
  - `test_invariants_property.py`：基于 `Hypothesis` 随机生成买卖配对序列，验证 Spec §5.7 的 6 条不变量。
  - `test_target_solver.py` 与 `test_ledger_service_db.py`：验证分段止盈求解、幂等防重、撤销回退与审计事件落库。

## 2. Validation Commands

```bash
cd backend
alembic upgrade head
pytest -v
ruff check app tests
mypy app
```

## 3. Risky Points & Rollback

- **风险点**：多笔部分卖出后最后一次清仓的尾差分摊顺序；并发或同一秒内多笔买入的 FIFO 稳定排序。
- **应对与回滚**：FIFO 严格使用 `(executed_at, created_at, id)` 三元组排序；若迁移调整，通过 `alembic downgrade base` 回滚。
