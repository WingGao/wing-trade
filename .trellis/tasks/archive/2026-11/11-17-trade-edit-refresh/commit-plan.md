# 交易流水编辑与待刷新：提交计划

## 状态与顺序

实现、独立质量检查和规格同步已完成：后端 105 项、前端 29 项测试通过，类型检查与构建通过；全库 161 项既有 Ruff 问题经逐条指纹比较无新增，见 [验证记录](validation.md)。用户已回复“是”，一次性批准以下计划。功能提交 `acd25c38046e653d99c99590df449106792e9a1c` 已完成，随后由脚本执行任务归档和会话记录。工作分支为 `feat/trade-edit-refresh`，不推送远端、不 amend。

## 1. 功能提交

提交信息：`feat: 支持交易流水编辑与持仓刷新提示`

该提交覆盖同一完整功能的后端事实编辑、持久状态与迁移、前端交互、测试和规格同步，共 30 个文件。

### 后端文件

- `backend/alembic/versions/0003_position_refresh_states.py`
- `backend/app/api/errors.py`
- `backend/app/api/schemas/dashboard.py`
- `backend/app/api/schemas/trade.py`
- `backend/app/api/v1/router.py`
- `backend/app/domain/exceptions.py`
- `backend/app/models/__init__.py`
- `backend/app/models/ledger.py`
- `backend/app/services/dashboard.py`
- `backend/app/services/ledger.py`
- `backend/app/services/position_refresh.py`
- `backend/app/services/recalculate.py`
- `backend/tests/integration/test_position_refresh_migration.py`
- `backend/tests/integration/test_trade_edit_refresh.py`

### 前端文件

- `frontend/e2e/mvp_ledger_flow.spec.ts`
- `frontend/e2e/trade_edit_refresh.spec.ts`
- `frontend/src/api/client.ts`
- `frontend/src/api/types.ts`
- `frontend/src/components/EditTradeDialog.vue`
- `frontend/src/pages/DashboardPage.vue`
- `frontend/src/pages/SecurityDetailPage.vue`
- `frontend/src/pages/TradesPage.vue`
- `frontend/src/stores/ledger.ts`
- `frontend/src/utils/recalculate.ts`

### 产品规格与层规范

- `.trellis/spec/backend/index.md`
- `.trellis/spec/backend/portfolio-batch-contracts.md`
- `.trellis/spec/frontend/index.md`
- `.trellis/spec/frontend/portfolio-detail-guidelines.md`
- `.trellis/spec/frontend/state-management.md`
- `docs/feat/ashare_personal_trading_spec.md`

## 2. 任务归档提交

功能提交成功后，按 Trellis 流程归档 `11-17-trade-edit-refresh`；由归档脚本产生 `chore(task): archive ...` 提交，包含本任务 PRD、设计、实施计划、上下文清单、研究、验证及本计划等材料。

## 3. 会话记录提交

归档后通过 `add_session.py` 记录中文会话摘要和功能提交哈希，由脚本产生 `chore: record journal`，不把归档提交哈希混入功能引用。

## 不纳入本批次的文件

- `.codegraph/`：实施前已有的未跟踪索引目录，保留原状，不暂存、不提交。
- `ledger.db`：真实本地账本，不执行迁移、删除或清空，也不纳入 Git 提交。
- `frontend/test-results/` 等测试产物：保留为本地验证资料，不加入功能提交。

以上清单已按独立检查后的实际改动核对；确认后不能暗中扩大提交范围。
