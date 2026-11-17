# 交易流水编辑与待刷新：独立质量检查报告

## 检查结论

2026-11-18，基于已批准的 PRD、设计、实施计划和各层规范完成全范围检查。发现并修复两处前端边界缺陷，补充两项能够复现问题的模拟端到端回归。功能、类型检查、构建及回归测试通过；全库 Ruff 仍未通过，剩余均为既有问题，已用逐条指纹比较确认本任务没有新增诊断。没有因处理历史 lint 而扩大修改范围。

## 已修复问题

### 1. 真实 SQLite 时间响应无法保存仅备注修改

- 证据：`backend/app/api/v1/router.py:89` 的 `_dt` 直接 `isoformat()`，`trade_data` 在 `:120` 用其序列化成交时间；SQLite 回读的时间可能无时区。新表单原本无条件要求时间带时区，即使没有修改时间也会阻止保存；原模拟数据始终带 `Z`，未覆盖真实响应形态。
- 修复：`frontend/src/components/EditTradeDialog.vue:18` 新增局部 `formTimestamp`，仅为缺失时区的既有 UTC 字符串追加 `Z` 作为显示和比较基准，不通过 `Date` 重建、不截断微秒。未修改时间仍不进入 PATCH，保持后端既有响应格式不变。
- 回归：`frontend/e2e/trade_edit_refresh.spec.ts:115`，`SQLite无时区UTC时间可编辑备注且不改写原始时间`。使用无时区且含六位微秒的响应，只改备注，断言提交仅有 `revision` 和 `memo`，原时间不变。修改前针对用例失败，修改后通过。

### 2. 重算后读取期间切换已结清条件会接受旧查询

- 证据：Dashboard 在 `mutationActive` 时直接跳过 `loadValuation`，开关变化没有推进读取代次。重算后的旧 `includeClosed=false` GET 在途时切成 true，不会立即读取新条件，旧响应仍可写入。
- 修复：`frontend/src/pages/DashboardPage.vue:59` 使用独立 `queryGeneration`；开关在写入期间变化时失效旧页面和 store 读取，重算的 `reload` 在查询条件稳定后才返回结果。已经发出的 POST 不受影响，被替代的读取不会误报为页面 GET 失败，真正读取失败仍独立提示。
- 回归：`frontend/e2e/trade_edit_refresh.spec.ts:196`，`重算后的读取期间切换已结清条件，丢弃旧读取并立即按新条件重读`。挂起重算后的 GET、切换开关、放回旧响应，断言立即发出新条件 GET、正常成功提示且没有读取失败提示。修复前等待新条件查询超时，修复后通过。

## 验证命令与实际结果

| 命令或检查 | 实际结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q`，工作目录 `backend/` | 105 项通过，99 条既有 FastAPI/Starlette 弃用警告 |
| `.venv/Scripts/python.exe -m mypy app`，工作目录 `backend/` | 54 个源文件通过 |
| `pnpm --dir frontend run type-check` | 通过 |
| `pnpm --dir frontend run build` | 通过；保留既有大于 500kB 的 bundle 体积提示 |
| `pnpm --dir frontend run test:e2e` | 29 项通过，耗时 13.1 秒；本功能文件共 11 项，含本次检查新增 2 项 |
| 新增模块、迁移、测试及相关小型改动文件 Ruff | 通过，详见下列 lint 证据 |
| `.venv/Scripts/python.exe -m ruff check app tests --output-format concise` | 未通过：161 项既有诊断，未新增 |
| `git diff --check` | 通过，仅 Git 的 CRLF 提示 |
| 本次后端变更方法注释 AST 检查 | 检查 40 个涉及变更的方法，缺少 docstring 为 0；前端新增和修改的具名方法也已逐项核对中文说明 |

所有数据库测试通过显式临时路径或内存库运行；迁移子进程显式设置临时 `DATABASE_URL`，应用测试使用 `create_tables=False` 和依赖替换。前端测试全部拦截模拟接口。检查过程中没有对真实 `ledger.db` 执行写入、迁移、删除或清空。

## Ruff 基线对比证据

在临时目录通过 `git archive HEAD backend` 还原已提交基线，使用当前同一个 Ruff 可执行环境分别检查基线与工作树。对每条诊断按以下四项建立带重复计数的指纹，而不是仅比较错误总数或易漂移的行号：

`相对文件路径 + 规则码 + 完整消息 + 诊断位置对应源码行（去首尾空白）`

- HEAD：164 项；当前：161 项；新增指纹：0。
- 当前规则分布：E501 119、UP017 34、I001 6、UP042 1、UP035 1。
- 减少的 3 项均位于 `app/models/ledger.py`：I001 导入块、F401 未使用 `TradeSide`、E501 旧长导入行。
- 新增 `services/position_refresh.py`、迁移 `0003_position_refresh_states.py`、两份后端新测试，以及 `recalculate.py`、`dashboard.py`、交易及 Dashboard schema、模型导出和领域异常文件的定向 Ruff 均通过。

## 验收标准映射

| 验收 | 检查与测试证据 |
|---|---|
| A1 | 后端有效买卖编辑及已撤销拒绝测试；前端完整预填、取消不写、已撤销禁用编辑。 |
| A2 | `test_edit_preserves_identity_precision_audit_and_old_projection` 与卖出价格、费用更正测试，核对 ID、来源、幂等标识、版本和前后审计。 |
| A3 | `test_invalid_or_forbidden_patch_has_no_partial_writes` 参数化拒绝非法数值、浮点、null、时间和越权字段；`test_stale_revision_voided_and_missing_trade_are_explicit` 核对 404/409/422。 |
| A4 | `test_noop_and_memo_clear_keep_original_state_semantics`、`test_edit_transaction_rolls_back_trade_audit_and_mark`，以及 UTC/Decimal 等值与仅备注微秒精度测试。 |
| A5 | `test_pending_closed_scope_survives_new_session_and_gets_do_not_write` 从同一临时文件重建 engine/session，前端重载仍由服务端状态显示提示。 |
| A6 | 同一持久化测试核对无活动行待刷新清单；前端活动仓位空行入口和详情无批次刷新均通过。 |
| A7 | 前端行情刷新和取消确认不发重算、不中断持久提示；后端 GET 不创建状态、不清标记。 |
| A8 | `test_refresh_rebuilds_cost_and_only_clears_target_scope`、`test_quantity_and_time_edit_rebuilds_remaining_position`；前端两处复用统一流程、正确更新；重复重算不新增事实。 |
| A9 | `test_failed_recalculation_retains_edit_old_projection_and_target` 参数化数量不足、先卖后买，检查编辑已保存而全部旧投影及止盈保留；`test_repeated_mark_clear_and_failure_after_clear_are_atomic` 在清除并 flush 后注入失败，确认末端回滚也恢复标记和投影。 |
| A10 | `重算提交成功但读取失败时明确区分，重进按服务端状态展示`；新增查询被替代回归确认不会误报真实 GET 失败。 |
| A11 | 后端同股跨账户、策略及证券隔离；前端快照账户与策略透传、确认期间离页、POST 期间切账户、写前迟到 GET 和切换已结清条件的回归。 |
| A12 | `test_refresh_migration_preserves_all_old_rows_and_reverses` 验证 0002→head→0002 旧表逐列不变、唯一约束和外键；注释、类型、构建及测试通过。全库 lint 的既有例外已逐条对比记录，不作为新增问题。 |

## 未修复事项与交付边界

- 本任务范围内未发现其他阻塞功能或验收的问题。
- 全库 161 项既有 Ruff 诊断尚未清理，原因是超出本任务范围；不能据此宣称全库 lint 通过。
- 既有弃用警告和构建体积提示保留，未增加框架迁移或构建拆包工作。
- 真实账本尚未执行新增迁移；本次隔离迁移验证不能当作部署库已升级。部署仍须核对实际库和 Alembic 基线。
- 显式止盈继续按现有重算语义恢复，保存的参考价和计划量不会自动重估；界面已明确提示核对。
- 本检查没有提交或归档任务，交由主会话完成验证记录、提交计划和 Trellis 后续收尾。
