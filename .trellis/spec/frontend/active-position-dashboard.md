# 活动仓位展示与排序契约

## 1. 适用范围

适用于首页 `DashboardPage.vue` 的活动仓位表，以及其复用的 `QuoteBadge.vue` 紧凑展示模式。目标是保证持仓成本、当前估值、报价状态和涨幅排序在刷新、过期及缺行情时保持一致。

## 2. 签名

- `QuoteBadge` 接收 `quote?: Quote | null`、`currency?: Currency | null`、`displayValue?: string | null`、`compact?: boolean`。
- 活动仓位向 `QuoteBadge` 传入 `displayValue=currentPositionValue(row)` 与 `compact`，禁止在模板中重复拼接报价来源和时间。
- 表格列使用 `prop="quote.changePct"`、`sortable="custom"`，默认排序为 `{ prop: 'quote.changePct', order: 'descending' }`。
- `handlePositionSort({ prop, order })` 仅处理 `quote.changePct`；`order` 为 `ascending`、`descending` 或 `null`。

## 3. 契约

- 持仓列标题为“持仓”。单元格第一行显示整数持股数并带“股”，第二行显示“加权成本 + 金额”，不得拆回两个重复列。
- 当前价值列标题为“当前总价值”。第一行显示 `remainingQty * quote.lastPrice` 的展示值，保留两位小数；第二行依次显示三位现价、`changePct` 和报价状态。
- 当前总价值仅用于展示。账务、持仓成本和浮盈仍以后端字段为准，不得将计算结果回写 API 或用本地排序结果替代服务端账务集合。
- `QuoteBadge` 的紧凑模式必须隐藏常驻的报价来源/时间，并通过 `el-tooltip` 将“报价更新信息”挂在“实时/已过期/不可用”标签上。提示至少包含可用时的行情来源、报价时间、获取时间和当前单价。
- 活动仓位默认按当前涨幅降序排列；点击“当前总价值”列头在降序和升序之间切换。无有效 `changePct` 的持仓必须在两种方向下都排在有效涨幅持仓之后。
- “最低价批次合计”第二行使用紧凑文本：`x批`、整数股数加“股”、两位小数成本金额，三者用间距分隔；不再显示“合计剩余”或“成本”标签。

## 4. 校验与状态矩阵

| 条件 | 界面行为 |
|---|---|
| 行情有效 | 显示实时总价值、三位现价、涨幅和“实时”标签 |
| 行情过期 | 保留参考总价值与现价，状态显示“已过期”，提示中保留报价来源和时间 |
| `quote=null` 或 `lastPrice=null` | 状态显示“不可用”，总价值不得补 0 |
| `changePct=null` 或非数字 | 该行不参与涨幅大小比较，固定在有效行之后 |
| 默认排序 | 按 `changePct` 降序 |
| 点击列头 | 在升序和降序之间切换，不取消涨幅排序 |
| 数据刷新 | 新快照仍沿用当前排序方向，不恢复后端原始顺序 |

## 5. 正常、边界与错误示例

- 正常：持仓 `600` 股、现价 `7.890`、涨幅 `+2.50%`，第一行显示总价值，第二行显示 `¥ 7.890  +2.50%  实时`。
- 边界：涨幅为 `0` 的持仓仍属于有效值，应先于缺行情持仓；缺行情持仓始终最后。
- 边界：总价值为 `0` 时显示 `¥ 0.00`，不能显示破折号；`lastPrice=null` 才显示不可用状态。
- 错误：点击列头后出现第三种“取消排序”状态，导致刷新后顺序不稳定。
- 错误：为了显示总价值把格式化后的金额字符串转换后回写持仓成本。

## 6. 必须测试

- Playwright 断言两个表头为“持仓”“当前总价值”。
- Playwright 断言总价值与现价位于不同行，第二行同时包含现价、涨幅和“实时”标签。
- hover 状态标签后出现“报价更新信息”，并包含行情来源及报价/获取时间。
- 使用至少三条持仓验证默认降序、点击列头升序，以及缺行情持仓在两种方向下均位于末尾。
- 验证最低价批次第二行 DOM 文本为 `2批150股1,200.00`，视觉间距由 CSS 提供。
- 运行 `pnpm run type-check`、相关 Playwright 用例和 `pnpm run build`。

## 7. 错误与正确写法

```ts
// 错误：缺行情时用 0 参与排序，导致不可用持仓混入有效涨幅。
const change = Number(position.quote?.changePct ?? 0)
```

```ts
// 正确：无效涨幅显式返回 null，并在比较器中将无效行固定放在末尾。
function positionChangeValue(position: DashboardPosition): number | null {
  const value = Number(position.quote?.changePct)
  return Number.isFinite(value) ? value : null
}
```

```vue
<!-- 错误：活动仓位把报价来源和时间常驻铺在价格下方。 -->
<QuoteBadge :quote="row.quote" />

<!-- 正确：传入总价值与紧凑模式，由 QuoteBadge 统一处理两行展示和 hover 提示。 -->
<QuoteBadge :quote="row.quote" :display-value="currentPositionValue(row)" compact />
```
