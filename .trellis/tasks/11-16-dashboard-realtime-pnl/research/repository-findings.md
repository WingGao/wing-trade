# Dashboard 行情与浮盈代码调研

检查日期：2026 年 11 月 16 日。本文记录规划证据，尚未修改产品代码。

## 运行时行情缺口

- `backend/app/api/v1/router.py:43` 创建 `QuoteCollector(MockQuoteProvider())`，未配置行情数据，也没有通过运行配置选择真实适配器。
- `backend/app/config.py:9-23` 仅配置数据库地址和 SQL 输出，没有行情源选择或凭据设置。
- `backend/app/market_data/provider.py:134-180` 实现可选的 AKShare 延迟加载，调用 `stock_zh_a_spot_em`，不包含美股报价获取。
- `backend/pyproject.toml:10-25` 没有声明 AKShare 或行情依赖扩展。
- `backend/app/api/v1/router.py:502-540` 返回 camelCase 行情载荷，将采集工作交给共享采集器。

## 刷新与快照一致性

- `frontend/src/pages/DashboardPage.vue:28-29` 先加载账务再读缓存行情，按钮只刷新行情状态，没有页面轮询生命周期。
- `frontend/src/stores/quotes.ts:10-16` 区分上游刷新和缓存读取；lastUpdated 是客户端接收时间，不是行情报价时间。
- `frontend/src/stores/ledger.ts:12-16` 独立加载服务端计算的持仓和汇总。
- `frontend/src/pages/DashboardPage.vue:85-87` 已展示汇总浮盈金额和持仓行情标签，但没有持仓浮盈率及最低价批次合计指标。
- `frontend/src/components/QuoteBadge.vue:6-11` 展示价格、涨幅和过期标记；需明确覆盖 null／零涨幅及报价／获取时间的展示。
- `backend/app/api/v1/router.py:372-376` 使用持久化缓存计算浮盈，但未将所用快照和过期状态带入持仓响应，设计需避免混用估值快照。
- `backend/app/market_data/collector.py:58-97` 已提供锁、最小 10 秒刷新间隔、失败回退及熔断退避，应复用该边界，而非从前端直连行情源。
- `backend/app/market_data/service.py:36-44` 默认只采集未结清持仓和自选证券。
- `backend/app/market_data/calendar.py:29-54` 规定交易时段缓存有效期为 10 秒、休市为 3,600 秒；第 57—64 行通过 USTradingCalendar 表达美股时段。

## 成本口径与最低价批次合并

- 用户明确要求：在当前持仓范围内，合并所有原始买入成交价等于最低价的有效剩余批次。同价不选唯一批次、不按时间决胜；先合计剩余成本和数量，再计算组浮盈率。
- `docs/feat/ashare_personal_trading_spec.md:142-166` 是现有剩余成本、浮盈口径依据：买入费用计入成本，不预估未来卖出费用。
- `backend/app/services/accounting.py:95-130` 使用 Decimal 计算剩余数量／成本、加权单位成本及浮盈金额，尚未计算浮盈率或最低价批次组。
- `backend/app/models/ledger.py:104` 在 Trade 保存原始成交价，第 153 行 Lot.buy_trade 可访问该交易。不能将成交价与第 148 行含费的 Lot.unit_cost 混淆。
- `backend/app/services/ledger.py:177-189` 按买入现金流建立批次；部分卖出后必须使用当前剩余数量／成本，不能使用原始数量／成本。
- `backend/app/api/v1/router.py:340-349` 在已解析的账户范围内按证券、策略聚合持仓；最低价合并也必须遵守这些边界。
- `backend/app/api/v1/router.py:380-390` 已筛选批次列表，并按时间提供确定性顺序。
- `backend/app/api/schemas/position.py:47-57` 与 `frontend/src/api/types.ts:35-50` 需要为新指标补充契约。路由当前直接返回字典，仅改 schema 不会改变真实响应。

## 既有范围约束

- `.trellis/tasks/archive/2026-11/11-05-m1-market-quote/prd.md` 已批准行情提供者抽象、共享缓存、交易时段 10 秒约定、过期回退，且禁止自动交易；WebSocket／SSE 当时不在范围内。
- `.trellis/tasks/archive/2026-11/11-06-us-equity-support/prd.md` 已批准只做多美股普通股票／ETF、独立 USD 账户和禁止混合换汇，且明确没有要求接入真实美股行情源。
- `frontend/src/api/client.ts:27-29` 的首页持仓和汇总请求未传 accountId；`backend/app/api/v1/router.py:342`、`:484` 使用默认账户。如果后续要求从首页访问两类市场，需明确设计账户导航，不能意外混合币种。
- `backend/app/api/v1/router.py:492-499` 当前可仅汇总有报价的部分批次。首页一致估值必须区分覆盖不完整与完整账户总额，不能把部分浮盈当成完整估值。

## 已收敛的规划结果

- 用户选择先做 A 股，并明确最低买入价下所有仍持有的批次需要合并。
- 行情源依据、一次有时限的 SH/SZ 实际接口探测及轻量批量接入选择记录在 `ashare-provider.md`；公开行情延迟不作保证。
- `../design.md` 定义单次一致估值响应、过期／覆盖不完整状态、轮询生命周期，以及不按时间选一批的最低价全部批次合并规则。
- 前后端质量和状态管理规范已加入上下文清单，task.py validate 已通过。产品实现尚未开始，仍待最新最终方案批准。
- 用户要求本任务文档使用中文；技术标识、接口、字段、路径和命令保留原文。

## 保留的原有变更

初始上下文报告未跟踪的 `.trellis/tasks/00-join-wing/`、`frontend/pnpm-workspace.yaml`。本任务没有修改这两项。
