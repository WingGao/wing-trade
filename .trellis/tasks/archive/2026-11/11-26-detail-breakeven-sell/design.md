# 个股详情页回本卖出测算技术设计

## 架构与模块边界
本功能为纯前端只读测算模块，不改动后端接口与本地账务数据，新增与变更严格限定在前端：

1. **纯计算工具**：`frontend/src/utils/breakevenSell.ts`
   - 输入：`OPEN` 持仓批次列表 `Lot[]`、模拟卖出价 `price`、市场与证券类型参数（`allowDecimalQty` / `lotStep`）、容差 `tolerancePct`（默认 `0.01`，即 ±1%）。
   - 输出：结构化测算结果 `BreakevenSellResult`（状态枚举 `OK | NO_LOSS_LOTS | NO_PROFIT_LOTS | NO_SOLUTION | INVALID_PRICE | NO_OPEN_LOTS`、汇总指标、选中批次分配明细、卖出后剩余仓位统计）。
   - 所有对外导出函数和核心内部辅助函数均提供完整中文注释。
2. **测算弹窗组件**：`frontend/src/components/BreakevenSellDialog.vue`
   - 使用 `el-dialog`（PC 端固定舒适宽度，移动端 `width="94%"` / 紧凑卡片布局，遵循 `.trellis/spec/frontend/mobile-high-density.md`）。
   - 接收 `lots`、`quote`、`currency`、`isFund`、`securityName`、`symbolLabel` props，双向绑定 `visible`。
   - 内部维护可编辑模拟卖出价 `simPrice`（打开弹窗时初始化为 `quote?.lastPrice`），通过 `computed` 实时调用 `calculateBreakevenSell()`。
3. **详情页入口集成**：`frontend/src/components/SecurityDetailPanel.vue`
   - 在 PC 端顶部操作栏与移动端折叠操作区分别增加「回本卖出」按钮，点击设置 `breakevenVisible = true`。
   - 挂载 `<BreakevenSellDialog />`，传入 `displayLots`、`currentQuote`、`currency`、`isFund` 等现有响应式数据。
4. **单元测试**：`frontend/src/utils/__tests__/breakevenSell.test.ts`（或项目现有 vitest 约定路径，实施前按 `package.json` 测试配置确认）。

## 核心算法设计

### 1. 批次预处理
从 `lots` 中过滤 `status === 'OPEN'` 且 `remainingQty > 0` 的批次：
- 单位含费成本 $c_i = \text{remainingCost}_i / \text{remainingQty}_i$。
- 按模拟卖出价 $P$ 分类：
  - 盈利批：$c_i < P$
  - 亏损批：$c_i > P$
  - 平本批：$c_i = P$（单股盈亏为 0，可视作中性缓冲）
- 若无亏损批 → 返回 `NO_LOSS_LOTS`（附带当前持仓整体浮盈率）。
- 若无盈利批 → 返回 `NO_PROFIT_LOTS`（附带当前持仓整体浮亏率）。

### 2. 步长与可用块模型
- **A 股（`step = 100`）**：
  - 每条批次的 `remainingQty` 拆分为：最多 `floor(remainingQty / 100)` 个 100 股标准手；若存在零股余数 `rem = remainingQty % 100 > 0`，则仅在整批全部卖出时携带该零股（或当整批 `< 100` 时作为一个不可拆分的整体单元）。
- **美股 / 场外基金（`allowDecimalQty = true`）**：
  - 支持连续小数（精度 `0.0001`），单批可在 `(0, remainingQty]` 内连续取值并向下截断到 4 位小数（避免超卖）。

### 3. 最大化可卖总数量的求解策略
设容差 $r = 0.01$，组合总卖出量 $Q = \sum x_i$，总买入成本 $C = \sum x_i c_i$，总市值 $V = P \cdot Q$。
容差条件：
$$-r \le \frac{V - C}{C} \le r \iff C_{\min}(Q) = \frac{P \cdot Q}{1 + r} \le C \le \frac{P \cdot Q}{1 - r} = C_{\max}(Q)$$

1. **检查全仓合计**（所有盈利批 + 平本批 + 亏损批全部卖出）：
   - 若全仓组合盈亏率已在 `[-r, +r]` 内 → 直接返回全仓方案（已是绝对最大值）。
2. **情况 A：全仓偏亏（全仓盈亏率 < -r）**
   - 盈利池不足以覆盖全部亏损批。为最大化总卖出数量 $Q$：
     - 保留全部盈利批（及平本批），获得最大正向盈余预算。
     - 将亏损批按单位成本 $c_i$ **从低到高**（单股亏损最小、最容易被对冲的批次优先）排序依次加入。
     - 当加入某条临界亏损批会使总盈亏率跌破 `-r` 时，计算该临界批次最多可加入的数量 $x_k$：
       - 由 $(C_{\text{cur}} + x_k c_k) \le \frac{P(Q_{\text{cur}} + x_k)}{1 - r}$ 解得连续上界：
         $$x_k^{\max} = \frac{\frac{P}{1-r} Q_{\text{cur}} - C_{\text{cur}}}{c_k - \frac{P}{1-r}}$$
       - 按步长向下取整（A 股取 100 股整数倍；小数股取 `0.0001` 整数倍），验证是否落在 `[-r, +r]`。
3. **情况 B：全仓偏盈（全仓盈亏率 > +r）**
   - 亏损缓冲不足以压低全部盈利批。为最大化总卖出数量 $Q$：
     - 保留全部亏损批（及平本批），获得最大负向缓冲。
     - 将盈利批按单位成本 $c_i$ **从高到低**（单股盈利最小、最接近现价的批次优先）排序依次加入。
     - 当加入某条临界盈利批会使总盈亏率超过 `+r` 时，计算该临界批次最多可加入的数量 $x_k$：
       - 由 $(C_{\text{cur}} + x_k c_k) \ge \frac{P(Q_{\text{cur}} + x_k)}{1 + r}$ 解得连续上界：
         $$x_k^{\max} = \frac{C_{\text{cur}} - \frac{P}{1+r} Q_{\text{cur}}}{\frac{P}{1+r} - c_k}$$
       - 按步长向下取整，验证是否落在 `[-r, +r]`。
4. **离散步长兜底与接近 0% 优化**：
   - 在 A 股 100 股离散步长下，若上述主路径取整后出现“少 1 手低于 -1%、多 1 手高于 +1%”的边界跳过现象，则在盈余侧/亏损侧尝试微调（如减少 1 手高盈利批以换取多配或精准落入 `[-r, +r]`），选择满足 `[-r, +r]`、包含盈利批与亏损批且总数量最大（同数量下 $|\text{returnPct}|$ 最小）的方案。
   - 若仍找不到任何满足条件的方案，返回 `NO_SOLUTION`。

## 兼容性与回滚说明
- 完全增量新增，不改变现有 API、数据结构与既有组件行为。
- 如需回滚，仅移除 `SecurityDetailPanel.vue` 中的按钮与弹窗引用，并删除新增工具与组件文件即可。
