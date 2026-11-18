# 个股详情持仓汇总实施计划

## 实施顺序

1. 扩展后端 `list_lots`：筛选 Open 批次，复用 `AccountingService.position_metrics` 生成 `summary`，保持现有顶层字段兼容。
2. 补充后端集成测试，断言部分卖出后的数量、成本、已实现收益、未实现收益，以及 `includeClosed=true` 时汇总不变。
3. 在 `frontend/src/api/types.ts` 增加 `LotsSummary` 与 `LotsRead.summary` 类型。
4. 在 `SecurityDetailPage.vue` 保存、失效并渲染持仓汇总；更新批次标题，避免重复展示已实现收益。
5. 扩展 Playwright 夹具和个股详情用例，覆盖四项汇总、已结清开关不影响汇总、行情缺失空值与涨跌色。
6. 执行质量检查，修复类型、构建、后端测试和 E2E 回归。

## 风险点

- `includeClosed=true` 时响应批次中混合 Open/Closed，汇总必须单独从 Open 批次计算。
- 无行情时不能使用 `0`；`AccountingService` 的 `None` 语义必须在 DTO 和 UI 中保留。
- 切换账户或证券后旧 lots 响应必须继续被现有代次校验丢弃，同时汇总也要随范围清空。
- 标题、告警、汇总和表格都在同一卡片内，样式需紧凑且不能形成嵌套卡片观感。

## 验证命令

```powershell
uv run --cache-dir .uv-cache --project backend python -m pytest backend/tests/integration/test_portfolio_lots.py -q
pnpm --dir frontend run type-check
pnpm --dir frontend run build
pnpm --dir frontend exec playwright test e2e/portfolio_enhancements.spec.ts
```

## 完成门槛

- [x] `LotsRead.summary` 契约、类型和页面渲染一致。
- [x] 汇总只统计 Open 剩余批次，已实现收益统计全部有效卖出。
- [x] 无行情与切换范围不存在伪造零值或旧值残留。
- [x] 定向测试、类型检查、构建和 E2E 通过；既有长行 lint 问题单独记录。
