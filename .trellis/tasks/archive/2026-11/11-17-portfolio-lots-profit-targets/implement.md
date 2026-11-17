# 执行计划

用户已授权直接执行到提交前 review，规划后直接启动，不重复索要实现许可。

用户完成 review 后明确要求“提交吧”；功能提交为 `aa10418`，随后按 Trellis 归档任务与记录日志，不推送远端。

## 执行顺序

1. [x] 读取 Trellis 与前后端规范，创建任务，研究现有实现。
2. [x] 固化研究、接口和上下文，启动任务。
3. [x] 后端：设置迁移、动态止盈、批次浮盈/倒序、隔离重建、EQUAL、市值和证券交易投影；隔离测试。
4. [x] 前端：市值/查名/三位价格，详情图生命周期，短 ID/浮盈/列内止盈，整数股数及交易证券；mock E2E。
5. [x] 主会话：外部证券/日内行情适配与接口、离线解析及失败回退测试，联调。
6. [x] 独立 Trellis 检查代理全量审查、修复并运行完整验证。
7. [x] 更新规范与验收，检查差异，保留未提交状态供 review。
8. [x] 实现追加 R11：lots 返回闭仓买卖明细，详情开关控制完整明细表；隔离测试覆盖多次卖出、跨批匹配、作废排除和开关隐藏，独立检查后更新验收。
9. [x] 用户批准提交，已创建功能提交 `aa10418`；按无PR的本地任务流程归档并记录验证结果，不修改原始账本。

## 文件责任

- 后端代理：backend/app/{services,domain,models,api/schemas}、现有 api/v1/router.py、新 Alembic 及账务测试；不改前端和新 market 模块。
- 前端代理：frontend/src/**、frontend/e2e/**；不改后端和任务文档。
- 主会话：任务/研究/规范，backend/app/market_data/security_info.py、backend/app/api/v1/market.py 与对应测试。

## 验证

- backend 虚拟环境：`python -m ruff check app tests`、`python -m mypy app`、`python -m pytest -q`。
- `pnpm --dir frontend run type-check`、`pnpm --dir frontend run build`、`pnpm --dir frontend run test:e2e`。
- 临时隔离 SQLite：Alembic upgrade head/downgrade base，不接触用户 ledger.db。
- 验证行情缺失/过期、继承 GET 不写库、重建稳定/回滚/范围隔离、等量与盈亏守恒、轮询停止和旧响应失效。
