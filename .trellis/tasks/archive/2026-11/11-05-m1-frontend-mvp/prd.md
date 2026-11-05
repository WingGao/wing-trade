# M1 前端工作台与容器化部署 (m1-frontend-mvp) — PRD

## Goal

基于 Vue 3 + TypeScript + Vite + Element Plus 构建 A 股个人交易管理与盯盘前端工作台，实现资产与持仓总览、个股批次明细与卖出配对工作台、已结清批次默认折叠与展开、分段止盈计划配置、交易流水与撤销审计、复盘统计以及 Docker Compose 一键部署与 Playwright E2E 自动化验收。

## Confirmed Facts

- 规范依据：[docs/feat/ashare_personal_trading_spec.md](../../../../docs/feat/ashare_personal_trading_spec.md) §10, §13, §14.3, §14.4。
- 前置依赖：必须先完成 [11-05-m0-ledger-api](../11-05-m0-ledger-api/prd.md) 与 [11-05-m1-market-quote](../11-05-m1-market-quote/prd.md)。
- 交互约定：默认采用 A 股**红涨绿跌**（可切换）并配合 `+`/`-` 号与文案语义；所有行情价格旁展示数据时间与 `已过期` 标签；前端不自行捏造账务盈亏，卖出配对实时调用服务端 `/api/v1/trades/sell/preview` 预览。

## Requirements

- **R-01 `/dashboard` 总览与自选股栏**：
  - 展示当前持仓总市值（若行情可用）、累计买入现金流、累计卖出回款、累计已实现收益、当前未实现毛浮盈（明确标注“未扣预计卖出费用”）。
  - 展示活动仓位表（证券、持股数、加权单位成本、最新价、浮盈额/率、策略累计回本参考价、最近止盈目标、行情时间）与自选股行情面板（添加/删除/手动刷新）。
  - 提供“隐藏已结清”默认开启开关，关闭后展示已完成交易周期（Cycle）汇总。
- **R-02 `/security/{market}/{symbol}` 个股详情与配对工作台**：
  - **持仓批次表（Lot Table）**：展示买入日期、初始/剩余股数、含费单位成本、已实现收益、毛浮盈、分段目标进度与状态。
  - **已结清折叠区**：顶部常驻显示“已结清 X 批、累计已实现收益 ¥Y”，展开可查看已结清 Lot 及其历史 `TradeMatch` 配对明细。
  - **买入录入弹窗 & 卖出配对面板**：卖出时默认填充 FIFO 推荐数量，允许用户手动修改各 Lot 分配股数，实时调用预览接口展示预计分摊成本与本笔净收益，确认后带 `client_request_id` 原子提交。
  - **分段止盈目标配置弹窗**：支持为未结清 Lot 添加/修改/删除多档目标（毛收益/净收益口径、计划卖出股数、参考目标价预览、超量冲突提示）。
- **R-03 `/trades` 交易流水与 `/analytics` 复盘视图**：
  - `/trades`：按证券、方向、策略、状态筛选流水，支持带原因撤销交易（弹窗提示受影响的配对与统计）并查看审计记录。
  - `/analytics`：按证券、策略、交易周期展示现金流、已实现收益、未实现浮盈与累计回本参考价。
- **R-04 容器化部署与文档**：
  - 提供 `compose.yaml`、前后端 `Dockerfile` 及 `README.md`（含一键启动、SQLite `./data` 持久化目录备份与恢复说明）。

## Acceptance Criteria

- [ ] Playwright E2E 测试完整覆盖 Spec §14.3：从空账本录入两批买入、一次卖出，页面展示数值与 Spec §6 完全一致；切换“隐藏/显示已结清”列表行数变化但总已实现收益不变；设置两档止盈目标并验证状态展示；清仓后 Cycle 自动归档且再次买入开启新 Cycle。
- [ ] 模拟后端行情接口返回 `is_stale=True` 或断网状态时，页面清晰展示“已过期/行情不可用”标识，且买卖录入与历史收益查看正常可用。
- [ ] `pnpm run type-check` 与 `pnpm run build` 零错误通过，`docker compose up --build` 可一键拉起前后端服务。

## Out of Scope

- K 线图叠加买卖点、CSV 导入界面、WebSocket 推送（归属 V1）。
