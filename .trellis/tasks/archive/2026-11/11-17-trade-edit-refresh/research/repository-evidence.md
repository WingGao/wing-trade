# 交易编辑与待刷新：仓库证据

## 调研方式与边界

2026-11-17，基线 `master`、`8de17bd`。先加载 `trellis-start`、`trellis-brainstorm`，创建并沿用本任务。仓库有 `.codegraph/`，先通过 `codegraph explore` 定位，再用精确符号/文件名和定点读取核实。中文语义查询无匹配后改用 `trade recalculate portfolio`，没有绕过 CodeGraph 优先规则。

本轮只写本任务规划文件，未调用账务写接口、未运行产品应用或真实库迁移，未删除或清空 `ledger.db`。默认终端出现 Windows `CreateProcessAsUserW failed: 5`，经 `require_escalated` 执行安全仓库命令成功。历史任务材料已包含相关决定，不再重复检索会话历史。

## E1：编辑缺口与已有字段

| 证据 | 结论 |
|---|---|
| `frontend/src/pages/TradesPage.vue:7-45` | 只有读取、方向筛选和撤销，没有编辑。继续保留代码/名称优先及时间倒序。 |
| `frontend/src/api/client.ts:48-53` | 只有列表、预览、买入、卖出、撤销；不存在编辑 API。 |
| `backend/app/api/v1/router.py:350-380`、`:496-526` | 列表默认排除 VOIDED；已有新增接口，没有交易 PATCH。 |
| `backend/app/models/ledger.py:89-128` | 保存账户/证券/策略/方向、成交字段、四项费用、备注、来源、幂等键、revision 和更新时间。 |
| `backend/app/api/schemas/trade.py:11-30`、`:52-78` | 请求/响应已有四项费用、备注、revision，金额为 Decimal。 |
| `frontend/src/api/types.ts:139-157` | 前端 Trade 尚缺费用明细和 revision，编辑前需补齐。 |
| `backend/app/services/ledger.py:64-91`、`:336-381` | 统一审计支持前后值、actor、reason；撤销递增 revision，但有有效卖出匹配的买入不可撤销。不能用撤销再新增模拟编辑。 |

仓库能回答“字段是否存在”，不能回答“本次应开放哪些字段”。调研时将编辑字段范围记为 Q1；2026-11-18 用户已采用推荐范围，该问题已关闭，确认结果见 PRD。

## E2：校验、精度与事务

- `backend/app/api/schemas/common.py:14-22`：camelCase 别名、拒绝二进制浮点；新增编辑请求需自行严格限制可写字段，不能假设全局已禁止多余字段。
- `backend/app/domain/models.py:12-28`：四项费用校验非负、规整至四位小数。
- `backend/app/services/accounting.py:34-80`：数量四位、价格六位、金额四位；数量和价格必须为正；费用只计入一次。
- `backend/app/api/utils.py:13-26`：SQLite 使用 `BEGIN IMMEDIATE`，路由负责 commit/rollback，适合原子保存编辑和范围标记。
- `backend/app/api/errors.py:18-25`：缺失交易 404、数量不足 409、校验错误 422；版本冲突需新增稳定错误码映射，不能误落为通用 400。

## E3：重算链路和成功/失败行为

- `backend/app/api/v1/router.py:969-990`：解析账户、策略；同一写事务调用 `RecalculationService.rebuild`，成功 commit、异常 rollback。
- `backend/app/services/recalculate.py:37-70`：范围严格限定账户、证券、策略。
- 同文件 `:71-105`：先备份旧 lot 标识和显式止盈，再删除派生匹配、目标、批次、周期，所有删除仍在同一事务。
- 同文件 `:107-179`：仅重放 ACTIVE，按 UTC 成交时间、同刻买入优先、创建时间和 ID 排序；卖出使用当前 EQUAL。无先前买入或数量不足会失败。
- 同文件 `:203-245`：恢复仍可关联的显式目标、追加审计、保留交易事实更新时间，返回批次/配对重建数。
- `backend/tests/integration/test_portfolio_lots.py:201-283`：验证事实和 revision 保留、其他股/策略隔离、lot ID/止盈保持、重复重建稳定。
- 同测试 `:286-315`：人为修改卖出至超量后，重算失败回滚恢复原批次/匹配/目标。
- 同测试 `:397-419`：已有接口连续重算和交易更新时间稳定性回归。

编辑保存与主动刷新是两个请求；刷新失败只回滚第二个事务，不能自动撤销已成功提交的编辑。方案必须保留更正入口和待刷新标记。

## E4：持久状态和范围

- 检查模型、迁移、前端类型，未发现持久化 `needsRefresh` 或等价字段。现迁移仅 0001、0002。
- `backend/app/api/deps.py:45-67`：省略账户会选默认人民币账户，省略策略会选 DEFAULT，不能用于首页任意行的定向刷新。
- `backend/app/services/dashboard.py:112-145`：先过滤批次再按证券和策略聚合，默认不读 CLOSED，无活动行时单靠行标签会漏提示。
- `backend/app/api/schemas/dashboard.py:39-88`：账户在顶层，行内有策略，无待刷新状态。
- `backend/app/api/v1/router.py:585-672`：lots 按账户/证券/策略读取；状态必须独立于是否有批次及 includeClosed。

因此建议新增按三元范围唯一的状态表，不给 Security 加全局布尔值，也不挂到重算会删除的 Lot/Cycle。

## E5：前端与异步风险

- `frontend/src/pages/DashboardPage.vue:140-169`、`:297-305`：顶部“刷新行情”只请求首页估值，不重算。
- 同文件 `:252-268`、`:347-374`：活动仓位无重算行操作，跳详情只带市场代码；快速记账账户不代表看板账户。
- `frontend/src/stores/ledger.ts:49-85`：已有串行队列、代次、自动请求复用，写后必须失效旧请求并关闭复用再读。
- `frontend/src/pages/SecurityDetailPage.vue:59-78`、`:87-143`：先行情后批次、每三秒串行轮询，客户端 lots 只传账户，尚未透传策略。
- 同文件 `:153-169`：已有确认、账户和证券代次保护；需复用并补全策略绑定。
- 同文件 `:147-149`、`:164-168`：reloadLots 捕获错误后仍可能弹重算成功，应区分重算失败与后续读取失败。
- 同文件 `:178-214`：原重算按钮无需有 OPEN 批次，可沿用空仓刷新能力。
- `frontend/e2e/portfolio_enhancements.spec.ts:175-200`：已有轮询、隐藏/离页、重算确认/取消回归。

## E6：旧规格与已知止盈边界

- `docs/feat/ashare_personal_trading_spec.md:77`、`:118`、`:356`、`:428`：旧规格要求审计更正、配对/周期随更正原子重算；`:404`、`:520` 要求更正原因说明和审计。当前用户指定延迟手动刷新优先，实施时要同步这些旧描述，但不能丢审计或只改缓存。
- `backend/app/services/recalculate.py:71-75`、`:203-206`：显式目标逐字段恢复，非重新估价或审核计划量。
- `backend/app/services/profit_settings.py:26-45`：LOT 目标显示已存参考价；`:46-65` 的继承目标才依据当前批次重估。
- `backend/app/models/ledger.py:181-199`：未保存历史估价费率，不能无损恢复自定义估价参数。本任务按复用旧重算的边界保留显式目标，提示核对，不悄悄引入新止盈策略。

## E7：安全验证入口

- `backend/tests/integration/test_portfolio_lots.py:39-99`：内存库和 FastAPI 依赖替换；`test_profit_settings_migration.py:12-56`：临时路径、显式 DATABASE_URL 的 Alembic 升降级。
- `backend/app/main.py:21-44`：默认启动 create_all；Alembic 管理的库应先迁移再启动；仅 create_all 的旧库须核对基线，不能自动 stamp。
- `backend/pyproject.toml:17-46`、`frontend/package.json:6-11`：Ruff、mypy、pytest、type-check、build、Playwright。存在 `backend/.venv/Scripts/python.exe`。
- 历史质量记录 `.trellis/tasks/archive/2026-11/11-17-portfolio-lots-profit-targets/validation.md:23-39` 记载 Ruff 遗留错误；这不是本轮测试结果，实施时须重新报告。

## 调研结论

技术事实已足够支撑规划，停止进一步扩展。2026-11-18 用户已确认只编辑成交时间、数量、价格、四项费用与备注，不涉及归属或方向变更；无需增加旧/新范围标记及双边重算。此调研结论形成于实施前；后续用户已在最终规划摘要后批准“开始实施”，现已完成实现、独立检查及规格同步，实际结果见 `../validation.md` 和 `../check-report.md`。
