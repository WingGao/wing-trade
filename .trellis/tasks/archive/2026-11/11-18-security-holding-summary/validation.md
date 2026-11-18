# 个股详情持仓汇总验证记录

## 验证环境

- 日期：2026-11-18
- 分支：`feat/trade-edit-refresh`
- 工作区包含其他并行任务改动；本次验证只针对当前任务文件。

## 验证结果

| 检查项 | 命令 | 结果 |
|---|---|---|
| 后端批次集成测试 | `uv run --cache-dir .uv-cache --project backend python -m pytest backend/tests/integration/test_portfolio_lots.py -q` | 通过，8 passed |
| 前端类型检查 | `pnpm --dir frontend run type-check` | 通过 |
| 前端生产构建 | `pnpm --dir frontend run build` | 通过，仅既有大 chunk 警告 |
| 个股详情 E2E | `pnpm --dir frontend exec playwright test e2e/portfolio_enhancements.spec.ts` | 通过，12 passed |
| 补丁格式检查 | `git diff --check` | 通过，仅有 CRLF 转换提示 |

## 覆盖结论

- 汇总只统计 Open 且剩余数量大于零的批次。
- `includeClosed` 切换不改变四项汇总。
- 无行情与无剩余持仓均返回 `unrealizedPnl=null`，页面以不同提示区分。
- 过期行情保留后端参考浮盈并显示过期说明。
- 批次读取失败或范围不一致时清空旧汇总，迟到响应不能回写当前范围。
