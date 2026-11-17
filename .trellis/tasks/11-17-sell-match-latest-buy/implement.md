# 实施与验证安排

## 进入实施前

- [x] 用户同意创建任务，已读取工作流、前后端规范和现有代码。
- [x] 已准备需求、设计、实施安排与子代理上下文清单。
- [x] 验证上下文清单，向用户展示最终方案并取得实施确认。
- [x] 运行 `task.py start`，再进入产品代码修改。

## 实施顺序

1. [x] 派发 Trellis 实施子代理，负责本任务代码与必要测试；主会话协调，避免同时修改同一文件。
2. [x] 先覆盖最近买入优先、跨批次、数量不足、同一时间与时间先后测试，再实现后端策略及 API 传递。
3. [x] 补充真实买入展示字段，测试手续费情况下的成交价及预览、正式记账一致性。
4. [x] 调整共享工作台的默认策略、列表、短编号及旧预览保护。
5. [x] 补充 Playwright 测试，验证默认策略、主要信息、明确批次提交和输入变化后的重新预览。
6. [x] 派发 Trellis 检查子代理，复核需求、兼容、类型与测试；主会话核对端到端契约和收尾。

## 验证

- 使用现有本地 Python 和 pnpm 环境，不引入新的运行依赖。
- `frontend`：`pnpm run type-check`、`pnpm run build`、`pnpm test:e2e`，可先单跑新增用例再执行回归。
- `backend`：匹配单测及 API 集成测试，再执行可用的 `ruff check app tests`、`mypy app`、`pytest -v`；基线问题单独记录。
- `git diff --check`，核对十进制精度、账户范围、审计、幂等及其他界面行为。
- 浏览器环境可用时检查抽屉主要列可读性、长编号提示和视口适配；未运行的检查如实记录。

## 完成条件

- [x] PRD 验收项有实际代码路径或回归证据；检查子代理完成复核。
- [x] 主会话已同步新增契约与任务记录。
- [x] 按用户明确要求未提交 Git commit，任务保持 `in_progress`。

## 验证结果

- [x] 后端全量：`.venv\Scripts\python.exe -m pytest -q`，`38 passed`。
- [x] 前端类型检查：`pnpm run type-check` 通过。
- [x] 前端构建：沙箱外重跑 `pnpm run build` 通过，仅有既有大 chunk 警告。
- [x] Playwright：`pnpm exec playwright test e2e/mvp_ledger_flow.spec.ts`，`7 passed`。
- [x] 定向 Ruff：`uv run ruff check app/services/matching.py tests/unit/test_matching.py` 通过。
- [ ] 后端全量 Ruff 与 mypy 仍有既有基线问题，本任务未扩大范围修复。
