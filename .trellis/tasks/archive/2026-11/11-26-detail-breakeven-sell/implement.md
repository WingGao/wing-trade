# 个股详情页回本卖出测算实施计划

## 实施清单
1. **核对前端规范与测试配置**
   - 加载 `trellis-before-dev`，查阅 `.trellis/spec/frontend/portfolio-detail-guidelines.md` 与 `mobile-high-density.md`，确认前端测试框架（Vitest / npm scripts）。
2. **实现核心测算工具**
   - 新建 `frontend/src/utils/breakevenSell.ts`，实现盈利批 + 亏损批组合回本测算算法（支持 A 股 100 股步长与零股规则、美股/基金 4 位小数、最大化可卖数量、同量优先接近 0%、边界状态识别）。
3. **编写单元测试**
   - 新建测试文件覆盖：
     - 正常偏亏场景（全用盈利批 + 浅亏批优先 + 临界批部分卖出）
     - 正常偏盈场景（全用亏损批 + 浅盈批优先 + 临界批部分卖出）
     - 全仓本身就在 ±1% 容差内场景
     - A 股 100 股步长、零股处理与离散无解兜底场景
     - 美股/场外基金 4 位小数测算场景
     - 边界场景（全盈利、全亏损、无持仓、非法价格）
4. **实现测算弹窗组件**
   - 新建 `frontend/src/components/BreakevenSellDialog.vue`，支持模拟卖出价输入与实时重算、汇总指标展示、盈利批/亏损批明细列表及 PC/移动端双端适配。
5. **集成到个股详情页**
   - 修改 `frontend/src/components/SecurityDetailPanel.vue`，在 PC 端顶部操作栏与移动端折叠操作区添加「回本卖出」按钮并挂载 `BreakevenSellDialog`。
6. **质量检查与验证**
   - 运行前端单元测试、类型检查（`vue-tsc` / `npm run build`）及代码规范检查，确保所有方法具备中文注释。

## 验证命令
- 前端单测：`cd frontend && npm run test`（以 `frontend/package.json` 实际脚本为准）
- 前端类型与构建：`cd frontend && npm run build`

## 风险点与回滚点
- 离散步长（A 股 100 股）下大额盈亏批次可能出现“加减 1 手跨越 ±1% 区间”的边界，需通过单元测试充分验证局部调整与无解提示。
- 所有改动均在前端增量完成，不影响本地 `ledger.db` 与后端账务逻辑。
