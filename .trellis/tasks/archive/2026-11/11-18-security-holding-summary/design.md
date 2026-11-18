# 个股详情持仓汇总技术设计

## 边界与原则

- 账务汇总由后端 `GET /api/v1/securities/{securityId}/lots` 返回，页面只保存和格式化响应。
- 复用 `AccountingService.position_metrics` 计算数量、剩余成本、已实现收益和未实现收益，避免浏览器聚合产生精度或口径分叉。
- 新增 `summary` 为附加字段，保留现有顶层 `realizedPnl` 以兼容当前前端调用方和已有测试。

## API 数据契约

`LotsRead.summary` 新增以下字段，全部为十进制字符串，未实现收益无行情时为 `null`：

```json
{
  "summary": {
    "remainingQty": "800.0000",
    "remainingCost": "8123.4500",
    "realizedPnl": "256.7800",
    "unrealizedPnl": "133.5500"
  }
}
```

- `remainingQty`：仅 `status=OPEN` 且 `remainingQty>0` 的批次合计。
- `remainingCost`：上述批次的 `remainingCost` 合计。
- `realizedPnl`：当前账户、证券、策略范围内全部有效卖出匹配的收益合计。
- `unrealizedPnl`：上述剩余批次按当前缓存价计算的 `remainingQty * price - remainingCost`；无行情或无 Open 批次时为 `null`。
- `includeClosed` 只控制 `lots` 和 `closedTradeDetails`，不改变 `summary`。

## 后端数据流

1. `list_lots` 先解析账户、策略和证券，读取当前范围全部批次。
2. 从查询结果中筛选 Open 且剩余数量大于零的批次，读取同一证券行情缓存。
3. 使用 `AccountingService.position_metrics(open_lots, latest_price=..., realized_pnl=realized)` 生成汇总。
4. 批次行继续逐个使用现有逻辑计算浮盈；响应增加 `summary`。
5. 无 Open 批次或行情不可用时，`AccountingService` 保持 `unrealized_pnl=None`，前端显示 `—`。

## 前端数据流与状态

- `frontend/src/api/types.ts` 新增 `LotsSummary` 并挂到 `LotsRead.summary`。
- `SecurityDetailPage.vue` 使用 `holdingSummary` 保存最近一次有效响应；`applyLots` 与批次原子替换。
- 切换证券、账户或加载失败导致范围失效时清空 `holdingSummary`，防止旧值串到新范围。
- 模板在批次表上方渲染四项指标；金额使用 `formatMoney`，数量使用 `formatQuantity`，涨跌色复用 `trendClass`。
- 总浮盈旁使用紧凑状态文字：无行情为“暂无有效行情”，过期行情为“参考浮盈 · 行情已过期”，避免把参考值误解为实时完整估值。
- 页面不执行金额相加、收益率换算或行情缺失兜底。

## 兼容与回滚

- API 为新增字段，已有调用方不受影响；顶层 `realizedPnl` 保持不变。
- 不需要数据库迁移或数据重建。
- 若需回滚，前端删除 `summary` 渲染并恢复标题中的已实现收益文字即可；后端保留附加字段不影响旧页面。

## 验证策略

- 后端集成测试覆盖数量、成本、已实现收益、未实现收益及 `includeClosed` 口径不变。
- 后端集成测试覆盖无行情总浮盈为 `null`。
- Playwright 覆盖四项标签与值、显示已结清切换后汇总不变、无行情空值和涨跌样式。
- 执行后端定向测试、前端类型检查、构建和相关 Playwright 用例。
