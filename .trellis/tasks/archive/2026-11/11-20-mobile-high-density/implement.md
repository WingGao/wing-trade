# 前端移动端高信息密度适配执行计划

## 1. 实施步骤清单

### Step 1：视口工具与全局外壳
- [ ] 新增 `frontend/src/utils/viewport.ts`：实现 `useViewport()`，监听 `max-width: 768px` 并暴露响应式 `isMobile`（所有导出函数带中文注释）。
- [ ] 更新 `frontend/src/styles.css`：
  - 将 `body { min-width: 1080px; }` 限定到桌面端媒体查询；
  - 新增移动端高密度通用样式类（`.m-topbar`、`.m-main`、`.m-tabbar`、`.m-section`、`.m-list`、`.num` 等宽数字、紧凑间距与细分割线）。
- [ ] 更新 `frontend/src/App.vue`：根据 `isMobile` 渲染移动端极简顶栏 + 底部 TabBar（总览 / 流水 / 复盘），桌面端保持原样。
- [ ] 微调 `frontend/src/pages/LoginPage.vue` 在手机窄屏下的间距与验证码行排布。

### Step 2：总览页与行情徽章
- [ ] 检查并微调 `frontend/src/components/QuoteBadge.vue`：确保紧凑模式在移动端窄列中右对齐不换行溢出。
- [ ] 更新 `frontend/src/pages/DashboardPage.vue`：
  - 移动端渲染紧凑工具条、2 行账户汇总网格、紧凑快速记账条；
  - 移动端渲染活动仓位 3 列高密度列表（全展开模式：含最低价批次合计、策略回本参考价、刷新持仓按钮），表头支持点击切换当前涨幅降序/升序；
  - 移动端渲染 2 列自选股行情卡片。

### Step 3：个股详情页与批次/明细组件
- [ ] 更新 `frontend/src/components/IntradayChart.vue`：移动端自适应高度与紧凑空态。
- [ ] 更新 `frontend/src/components/LotTable.vue`：移动端渲染高密度批次列表（短 ID、状态、时间、设置按钮、股数/成本/浮盈、止盈目标）。
- [ ] 更新 `frontend/src/components/SettledTradeTable.vue`：移动端渲染高密度已结清买卖明细列表。
- [ ] 更新 `frontend/src/pages/SecurityDetailPage.vue`：移动端紧凑头部、操作条与 2x2 汇总样式。

### Step 4：交易流水页与复盘分析页
- [ ] 更新 `frontend/src/pages/TradesPage.vue`：移动端渲染紧凑方向筛选 + 高密度流水列表（含编辑与撤销入口）。
- [ ] 更新 `frontend/src/pages/AnalyticsPage.vue`：移动端渲染 2x2 紧凑指标 + 交易周期列表。

### Step 5：全部业务弹窗与抽屉
- [ ] 更新 `frontend/src/components/BuyTradeDialog.vue`：移动端 `94vw` + 顶部标签表单。
- [ ] 更新 `frontend/src/components/EditTradeDialog.vue`：移动端 `94vw` + 顶部标签表单。
- [ ] 更新 `frontend/src/components/ProfitSettingDialog.vue`：移动端 `94vw` + 顶部标签表单。
- [ ] 更新 `frontend/src/components/ProfitTargetDialog.vue`：移动端 `94vw` + 顶部标签表单。
- [ ] 更新 `frontend/src/components/SellMatchDrawer.vue`：移动端 `100%` 宽度 + 顶部标签表单 + 紧凑配对预览列表。

### Step 6：测试与质量验证
- [ ] 新增 `frontend/e2e/mobile_layout.spec.ts`（mocked API，覆盖 `390x844` / `360x780` 全业务页与弹窗零横向溢出及核心交互）。
- [ ] 运行类型检查、构建和全部 Playwright E2E 测试。

## 2. 验证命令

在 `frontend/` 目录下执行：
```powershell
pnpm run type-check
pnpm run build
pnpm run test:e2e
```

## 3. 检查点与回滚点

- **检查点 1（完成 Step 1~2）**：确保桌面端和移动端总览渲染正常且 `pnpm run type-check` 通过。
- **检查点 2（完成 Step 3~5）**：全页面与弹窗改造完成，`pnpm run type-check && pnpm run build` 通过。
- **检查点 3（完成 Step 6）**：桌面端既有 E2E + 移动端新 E2E 全部绿灯。
- **回滚策略**：所有改动均局限在 `frontend/src/` 与 `frontend/e2e/`，不涉及后端与数据库，可按 git 文件粒度安全回退。
