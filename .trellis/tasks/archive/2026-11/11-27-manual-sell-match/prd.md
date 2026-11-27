# 卖出支持手动关联买入批次

## 目标与用户价值

在卖出配对工作台（`SellMatchDrawer`）中支持用户手动选择本次卖出关联的未结清买入批次（Lot）及各批次配对数量，同时保留默认的「自动等量配对（EQUAL）」模式。用户可按实际持仓管理需求定向卖出高价批、盈利批或指定批次，减少手工核对成本。

## 背景与代码锚点

- **后端已原生支持手动配对**：
  - `POST /api/v1/trades/sell/preview`（`backend/app/api/v1/router.py:869`）：传入 `matches: [{ lotId, matchedQty }]` 时走 `MatchingMethod.MANUAL`，由服务端完成校验与精确 Decimal 分摊计算。
  - `POST /api/v1/trades/sell`（`backend/app/api/v1/router.py:895`）：传入 `matches` 时按 `MatchingMethod.MANUAL` 持久化 `TradeMatch`。
  - `MatchingService.validate_manual_matches`（`backend/app/services/matching.py:231`）：服务端校验批次不重复、数量为正、同账户/证券/策略归属、买入时间不晚于卖出时间（`FutureLotError`）、不超过批次可用量（`InsufficientLotQtyError`）、匹配数量之和等于卖出总量（`MatchQuantityMismatchError`）。
- **前端复用入口**：
  - `frontend/src/components/SellMatchDrawer.vue`：被 `frontend/src/components/SecurityDetailPanel.vue:862` 与 `frontend/src/pages/DashboardPage.vue:677` 复用，两者均已传入 `lots: Lot[]`。
  - `frontend/src/components/LotTable.vue:199,226,288`：桌面端与移动端持仓批次行及同价合并组子行均提供「卖出」按钮，触发 `sell` 事件到 `SecurityDetailPanel.vue:450`。

## 需求规格

### R1. 卖出配对模式切换（自动 / 手动）

1. 在 `SellMatchDrawer.vue` 中提供「自动等量配对 / 手动关联批次」切换控件，顶部「卖出配对」入口打开时默认选中「自动等量配对」。
2. 切换模式时自动使旧预览失效（`invalidatePreview`），确保提交前必须基于当前模式与输入重新预览。
3. 在「自动等量配对」模式下，保持现有请求参数（`matchingMethod: 'EQUAL'`）与预览展示不变。

### R2. 手动关联批次选择与数量分配

1. 切换到「手动关联批次」模式后，展示当前标的 `status === 'OPEN'` 且 `remainingQty > 0` 的全部未结清批次列表，每项显示：
   - 勾选框、买入时间、原始买入价/净值（优先 `purchasePrice`，缺失时回退 `unitCost`）、当前可用数量、批次短码（完整 ID 放在 `title` 提示）。
   - 配对数量输入框（跟随 `allowDecimalQty` 区分 A 股整数与基金/美股小数，支持最多 4 位小数）及单行「填满」快捷操作。
2. 提供批量辅助与实时汇总栏：
   - 展示「已选批次数」「已分配数量」「目标卖出数量」与差额状态；
   - 提供「同步为卖出数量」快捷按钮（将已分配合计直接回填到上方「卖出数量/赎回份额」输入框，支持先选批次再定总量）与「清空选择」按钮；
   - 若某批次买入时间晚于当前填写的卖出成交时间，给予清晰视觉标注提示（仍以服务端校验为准）。
3. 手动模式下提示文案明确说明：「手动指定卖出关联的买入批次及数量；如后续编辑历史成交并执行『重新计算』，系统仍按默认等量配对重放。」
4. 预览提交前进行基础前端校验：至少选择一个批次、每条已选配对数量为正且不超过该批次可用量、各批次配对数量之和与总卖出数量一致（使用纯函数精确小数规整/比较，避免浮点误差），校验通过后调用 `api.previewSell` 传入 `matches`（不传 `matchingMethod`）。
5. 预览成功后展示服务端返回的分摊成本、预计净回款、已实现收益与配对明细；确认卖出时沿用当前预览的 `matches` 调用 `api.sell`。任何手动勾选、数量、价格、时间、费用或模式变化均立即失效旧预览并禁用「确认卖出」。

### R3. 从自动预览结果一键转为手动调整

1. 在「自动等量配对」预览结果区域提供「转为手动调整」按钮。
2. 点击后切换到「手动关联批次」模式，自动勾选本次自动预览命中的批次并预填各自的 `matchedQty`，同时保留当前表单的卖出数量、价格、时间与费用，方便用户在其基础上微调后重新预览。

### R4. 持仓批次行「卖出」按钮联动预选

1. 扩展 `LotTable.vue` 的 `sell` 事件载荷，从仅传递 `remainingQty: string` 升级为传递结构化载荷（包含目标总数量及对应的预选批次列表 `{ lotId, matchedQty }[]`）：
   - 单批次行（含桌面端与移动端展开子行）：预选该单个批次及其 `remainingQty`。
   - 同价合并组行：预选该合并组下所有 `OPEN` 子批次及其各自 `remainingQty`，总量为组内剩余量合计。
2. `SecurityDetailPanel.vue` 接收后传给 `SellMatchDrawer.vue`（通过可选 prop 如 `initialMatches`）。
3. 当 `SellMatchDrawer.vue` 打开且存在非空 `initialMatches` 时，自动切到「手动关联批次」模式并预选对应批次与数量，同时预填 `form.qty`；当通过顶部「卖出配对」按钮打开（无 `initialMatches`）时，重置为「自动等量配对」模式。

### R5. 移动端适配与代码规范

1. 手动关联批次列表在桌面端使用紧凑表格，在移动端（`isMobile`）使用卡片列表，严格遵循 `.trellis/spec/frontend/mobile-high-density.md`，无横向溢出。
2. 将手动关联的纯逻辑（批次筛选、精度安全的合计/差额计算、合法性校验、构造 `matches` 载荷、从自动预览转换、从 `initialMatches` 初始化）抽离到独立工具模块（如 `frontend/src/utils/manualSellMatch.ts`），所有方法提供中文注释，并编写单元测试。

## 范围之外（Out of Scope）

- **不修改后端代码**：不改动 `RecalculationService.rebuild`（重算仍按 `EQUAL` 重放），不改动后端 API 或数据库结构。
- **不联动回本卖出测算弹窗**：`BreakevenSellDialog.vue` 保持纯测算弹窗定位，本次不新增“去卖出”跳转。
- **不新增卖后修改既有卖出关联功能**：仅支持卖出记账时的手动关联。

## 验收标准

1. **模式切换与默认兼容**：从首页或个股详情页顶部点击「卖出配对」打开抽屉，默认处于「自动等量配对」模式，原有预览与提交流程完全不受影响。
2. **手动勾选、预览与提交**：切换到「手动关联批次」后，可勾选任意一个或多个未结清批次并输入配对数量；点击「同步为卖出数量」可回填总卖出量；点击预览向 `/api/v1/trades/sell/preview` 发送 `matches`；预览成功后可正常点击「确认卖出」完成记账，并准确扣减所选批次剩余数量。
3. **输入变更防呆**：手动模式下修改任意批次勾选、数量、总卖出量、价格、时间、费用或切换模式时，旧预览立即清空且「确认卖出」按钮禁用。
4. **校验反馈**：未选批次、单批数量非法/超可用量、或已分配合计与总卖出量不一致时，前端给出明确提示并阻止无效预览请求；卖出时间早于所选批次买入时间时，展示提示并能正确显示服务端报错。
5. **自动转手动**：在自动等量配对预览后点击「转为手动调整」，能无缝切至手动模式并保留当前自动配对的批次勾选和数量供微调。
6. **批次行直达手动关联**：在个股详情页 `LotTable` 点击单批次或同价合并组的「卖出」按钮，抽屉打开后自动处于「手动关联批次」模式且已预选对应批次及剩余数量、预填总卖出数量；再次点击顶部「卖出配对」时恢复默认自动模式。
7. **响应式与测试通过**：桌面端与移动端（≤768px）均无横向溢出；前端单元测试、类型检查（`npm run typecheck` / `vue-tsc`）及构建均通过。
