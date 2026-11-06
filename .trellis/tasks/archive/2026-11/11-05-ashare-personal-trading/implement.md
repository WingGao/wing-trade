# A 股个人交易管理与盯盘系统 (MVP) — Execution Plan

## 1. Execution Order Across Child Tasks

父任务本身不直接承载业务代码编写，所有代码交付按以下子任务顺序推进，每个子任务独立完成编码、测试、质量检查与提交：

- [x] **Step 1: `11-05-m0-ledger-domain-db`（M0 账本领域模型与数据库）**
  - 建立 `backend/` 项目结构、SQLAlchemy 模型、Alembic 初始迁移。
  - 实现 `AccountingService`、`MatchingService`、`CycleService`、`TargetService`。
  - 通过 Spec §6 案例测试与 §5.7 Hypothesis 属性测试。
- [x] **Step 2: `11-05-m0-ledger-api`（M0 账本 REST API）**
  - 实现 `/api/v1` 下证券、自选股、买入、卖出预览/提交、撤销、持仓、批次、周期、止盈目标、统计汇总接口。
  - 通过幂等、并发锁、事务回滚与审计日志集成测试。
- [x] **Step 3: `11-05-m1-market-quote`（M1 行情采集与缓存）**
  - 实现 `QuoteProvider` 接口、AKShare 适配器、服务端缓存、交易日历判断与 `/api/v1/quotes` 接口。
  - 通过行情过期降级、熔断与离线隔离测试。
- [x] **Step 4: `11-05-m1-frontend-mvp`（M1 前端工作台与容器化部署）**
  - 实现 Vue 3 + TypeScript + Element Plus 四大核心视图、Docker Compose 编排与 Playwright E2E 验收测试。

## 2. Cross-Child Validation Commands

在全部子任务完成后，执行全栈端到端验收：

```bash
# 1. 后端全量单元与集成测试（含 Hypothesis 属性测试）
cd backend && pytest -v

# 2. 前端类型检查与构建验证
cd frontend && pnpm run type-check && pnpm run build

# 3. 容器化启动与健康检查
docker compose up -d --build
curl -f http://localhost:8000/health
```

## 3. Rollback Strategy

- 每个子任务均以独立 Git Commit 批次提交；若下游子任务发现上游领域契约缺陷，优先回到对应上游子任务修正模型/迁移并补充回归测试。
- 数据库迁移支持 `alembic downgrade -1` 回滚。

## 4. Final Validation Record (2026-11-06)

- Backend `python -m pytest -q`: **10 passed**.
- Backend `python -m compileall -q app tests`: **passed**.
- Frontend `pnpm run type-check`: **passed**.
- Frontend `pnpm run build`: **passed** (Rollup emitted a chunk-size warning).
- Frontend Playwright MVP suite: **2 passed**.
- `docker compose config`: **passed**. Full image build/startup was not completed
  in this environment because the Docker image pulls did not finish; `/health`
  and `/ready` were therefore unavailable.
- `python -m ruff check app tests` and `python -m mypy app` report existing
  formatting/type findings; these are recorded as follow-up cleanup rather than
  silently treated as passing.
