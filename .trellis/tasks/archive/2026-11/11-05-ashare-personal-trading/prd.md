# A 股个人交易管理与盯盘系统 (MVP) — Parent PRD

## Goal

构建一个以 **A 股交易流水** 为唯一可信事实源、支持按买入批次（Lot）精细化配对（FIFO 与手动指定）、支持分段止盈计划与自选股行情展示的自托管个人投资管理系统（A-Share Portfolio Journal）。核心价值在于确保多次分批建仓、分批减仓场景下，剩余股数、含费单位成本、已实现收益、未实现浮盈与交易周期（Cycle）累计回本参考价在数学上严格自洽且可审计。

## Confirmed Facts

- 规范基线：[docs/feat/ashare_personal_trading_spec.md](../../../../docs/feat/ashare_personal_trading_spec.md)（v0.1）。
- 目标市场与币种：第一阶段针对沪深 A 股普通股票（预留北交所 `BJ` 及 ETF 扩展），记账货币为 `CNY`，时区为 `Asia/Shanghai`（数据库统一存储带时区时间戳或 UTC）。
- 精度约束：数量 `DECIMAL(20,4)`，价格 `DECIMAL(20,6)`，金额与费用 `DECIMAL(20,4)`；全部账务计算使用 `decimal.Decimal`，严禁使用二进制 `float`。
- 默认产品决策（Spec §17）：
  - 买卖配对以用户手动指定为核心，默认提供 FIFO 推荐预配对，不静默篡改用户已提交配对。
  - 本地 SQLite（WAL 模式）持久化，单用户内网 MVP 默认免登录鉴权。
  - 每笔成交归属单一策略（默认 `DEFAULT`），跨策略卖出通过录入前拆单处理。
  - 真实交易费用逐笔录入（佣金、印花税、过户费、其他），预估费用模型仅用于止盈目标价测算。
  - 已结清批次（`remaining_qty == 0`）在日常持仓视图默认隐藏，但历史流水与收益统计永久保留。

## Child Task Mapping

本父任务拆分为 4 个可独立开发、独立验证、按序交付的 MVP 子任务：

1. **[11-05-m0-ledger-domain-db](../11-05-m0-ledger-domain-db/prd.md)**（M0 账本领域层与数据库）：
   - 交付 `backend/` 工程骨架、SQLAlchemy 2.x 核心表与 Alembic 迁移、纯 Python 领域核算与配对服务（`AccountingService`、`MatchingService`、`CycleService`、`TargetService`）以及 Spec §5.7 对账不变量属性测试与 §6 验收案例单元测试。
2. **[11-05-m0-ledger-api](../11-05-m0-ledger-api/prd.md)**（M0 账本 REST API）：
   - 依赖 `11-05-m0-ledger-domain-db`。交付 FastAPI `/api/v1` 账本接口（证券/自选股、买入建批、卖出预览与原子配对提交、`client_request_id` 幂等、单写事务防并发超卖、交易撤销与 `audit_events` 审计、持仓/批次/周期/止盈目标接口）及 API 集成测试。
3. **[11-05-m1-market-quote](../11-05-m1-market-quote/prd.md)**（M1 行情采集与缓存）：
   - 依赖 `11-05-m0-ledger-api`。交付 `QuoteProvider` 抽象、AKShare 适配器、服务端共享缓存（`quote_cache`）、交易时段调度与指数退避熔断、`is_stale` 过期判定及 `/api/v1/quotes` 接口，并验证断网/行情源故障不阻塞账务操作。
4. **[11-05-m1-frontend-mvp](../11-05-m1-frontend-mvp/prd.md)**（M1 前端工作台与容器化部署）：
   - 依赖 `11-05-m0-ledger-api` 与 `11-05-m1-market-quote`。交付 Vue 3 + TypeScript + Vite + Element Plus 前端页面（`/dashboard` 总览、`/security/{market}/{symbol}` 批次明细与卖出配对面板、已结清折叠、分段止盈设置、`/trades` 流水与撤销、`/analytics` 汇总）、`compose.yaml` 部署配置与 Playwright E2E 测试。

## Requirements

- **F-01 股票基础信息与自选股**：支持按代码/名称搜索证券（唯一标识 `market + symbol`，如 `SH:600519`），添加、删除、排序自选股。
- **F-02 手动录入买卖成交与费用**：支持录入成交时间、价格、数量、四类明细费用（佣金、印花税、过户费、其他）、策略与备注。
- **F-03 按批次形成持仓（Lot）**：每笔有效买入成交创建唯一 `Lot`，记录初始含费总成本与初始数量。
- **F-04 指定买卖配对与 FIFO 默认配对**：支持一卖多买、一买多卖及部分成交；卖出预览默认给出 FIFO 方案，支持手动调整各批次匹配股数并原子提交。
- **F-05 已结清批次折叠/展开**：`remaining_qty == 0` 的 Lot 状态置为 `CLOSED`，日常持仓默认折叠，切换“显示已结清”可查看历史配对明细，且折叠开关不改变任何收益统计。
- **F-06 剩余数量、成本与盈亏核算**：严格执行 Spec §5 公式与尾差归最后一段规则，区分会计持仓成本、已实现收益、未扣预计卖出费用的毛浮盈、周期累计回本参考价（`strategy_breakeven_price`）。
- **F-07 每批次分段目标收益与计划卖出量**：支持按毛收益率或扣预估卖出费净收益率设置多档止盈目标，校验计划卖出总量不超过当前剩余股数，计算参考目标价并维护提醒状态。
- **F-08 自选股行情与手动刷新**：服务端统一采集自选股与持仓行情，展示最新价、涨跌幅、`source_time`、`fetched_at` 与 `is_stale` 过期标签；失败时回退缓存。

## Cross-Child Acceptance Criteria

- [ ] 用户可从空数据库完整走通：新增证券 → 录入两批买入 → 预览并提交指定批次卖出 → 验证部分/全部抵消 → 切换隐藏/显示已结清 → 查看已实现/未实现收益与策略回本参考价 → 设置分段止盈目标 → 查看行情与过期状态。
- [ ] Spec §6 典型验收案例（Lot A 1000@10、Lot B 1000@9、S1 1000@11 匹配 Lot A，以及拆分配对 600+400、超额卖出 2100 拒绝录入）自动化测试全部通过。
- [x] Spec §5.7 六条对账不变量在单元属性测试（Hypothesis）与 API 集成测试中通过，全链路无二进制 `float` 参与金额计算。
- [ ] 相同 `(account_id, client_request_id)` 重复提交不产生重复账本记录；并发卖出争用同一 Lot 时不会出现 `remaining_qty < 0`。
- [ ] 断开外网或模拟行情源异常时，账本录入、配对、撤销、查询接口及前端账务操作完全可用，行情区域明确展示过期/不可用状态。
- [x] 止盈目标触发仅更新提醒状态，不存在任何自动下单或直接将卖出成交价篡改为目标价的行为。

## Out of Scope (MVP)

- 券商实盘交易接口接入、自动下单或持仓同步（系统永不接入）。
- CSV/Excel 批量导入、K 线买卖点叠加、分红送股拆分等公司行动调整、WebSocket/SSE 实时推送（归属 V1 / M2-M3）。
- XIRR / TWR 等复杂时间加权收益率指标（归属 V2）。
- 场外基金、港美股、Level-2 行情与多租户云同步。
