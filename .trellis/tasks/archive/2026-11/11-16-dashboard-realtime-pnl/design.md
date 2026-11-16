# Dashboard A 股实时行情与浮盈设计

## 架构与数据流

`东方财富批量 HTTP 行情 → QuoteProvider → 共享采集器／缓存 → 固定行情快照映射 → 后端估值 → 类型化 Dashboard DTO → Pinia → 首页`

以账务事实和 Decimal 核算结果为准。行情工作线程只返回普通报价快照，不修改交易、批次或数据库会话。复用 `QuoteService`、交易日历、采集器节流／退避，以及只由有效行情触发的止盈提醒逻辑。

## 行情源与缓存

- 按 `research/ashare-provider.md` 新增可测试的同步 `EastmoneyQuoteProvider`，使用标准库 HTTP／JSON，无需引入 AKShare／pandas 依赖。
- 增加带校验的 `QUOTE_PROVIDER=eastmoney|mock` 配置，默认 eastmoney；`QUOTE_TIMEOUT_SECONDS` 为有限正值，默认 5 秒。网络调用延迟到真正刷新时发起，导入模块、健康检查和纯账务操作必须支持离线。
- 保留测试中的模拟行情注入方式及现有 `/quotes` 契约。真实行情源排除不支持的美股条目，只请求持仓／自选范围内的 SH/SZ/BJ 证券。
- 复用共享采集器串行化、至少 10 秒的上游请求间隔，以及连续三次失败后的指数退避。支持的证券范围为空时不调用上游。
- 限制 HTTP 耗时、响应大小和多批请求的累计工作量。不能丢弃仍在运行的同步工作线程后又发起重叠请求，也不能让迟到结果写入已关闭的数据库会话。
- 只写入有效报价。请求范围内缺失或无效的证券保留旧价格和时间戳，并标为过期。部分更新后返回完整范围的缓存视图。全部无效或空的上游响应不能错误地清零失败状态。
- 报价时间来自上游，获取时间来自本地接收时刻。不能把旧数据重新标成新报价。按后端规范记录结构化行情降级日志。

## 一致的首页估值接口

新增 `GET /api/v1/dashboard`，支持可选 `accountId`、`includeClosed=false`、`mode=cache|auto|manual`，mode 默认 auto。

- `cache`：只读账务事实和缓存估值，不访问上游，用于快速首屏和纯账务降级。
- `auto`：按现有日历判断是否需要请求行情：交易时段间隔 10 秒、休市间隔 1 小时，缺少所需缓存时也可获取。仍受采集器最小间隔和熔断约束。
- `manual`：即使休市缓存尚未到期，也请求刷新，但继续遵守共享节流和熔断退避。
- 单次构建捕获统一当前时间，用于调度和新鲜度判断。交易时段返回 `pollAfterSeconds=10`，休市返回 60，Vue 不重复实现市场时区逻辑。
- 行情范围包含当前账户持仓和既有持仓／自选证券范围，保留自选股展示，美股代码不发往 A 股行情源。
- 在行情边界内消化获取失败，仍返回账务事实及过期／不可用估值。采集后固定行情快照映射，所有持仓、最低价批次组和账户估值均使用该映射。
- 按现有边界解析账户并查询账务。仅将必要的可复用读取／查询逻辑从路由提取到聚焦服务，例如 `app/services/dashboard.py`；不能把 FastAPI 路由函数当成服务调用，也不能重复抄写核算公式。
- 保持现有 `/positions`、`/analytics/summary`、`/quotes` 兼容。提取共享逻辑时，保留字段别名、已实现现金流和查询范围语义。

公开 DTO 契约采用 camelCase，精确十进制数值仍使用字符串：

```text
DashboardRead
  accountId: string
  currency: CNY | USD
  asOf: ISO 格式的估值构建时间戳
  marketSessionOpen: boolean
  pollAfterSeconds: 10 | 60
  positions: DashboardPosition[]
  summary: DashboardSummary
  quotes: Quote[]

DashboardPosition = 既有 Position 字段，加上
  quote: Quote | null
  unrealizedReturnPct: string | null
  lowestPriceGroup: {
    purchasePrice: string,
    lotCount: integer,
    remainingQty: string,
    remainingCost: string,
    unrealizedPnl: string | null,
    unrealizedReturnPct: string | null
  } | null

DashboardSummary = 既有 AnalyticsSummary 字段，加上
  unrealizedReturnPct: string | null
  valuationStatus: fresh | stale | partial | unavailable | empty
  totalOpenPositions: integer
  quotedOpenPositions: integer
  staleOpenPositions: integer
```

复用现有 Quote 字段，包括 sourceTime／fetchedAt／provider／isStale。每项持仓展示的报价必须与计算该项浮盈的报价一致；不能由单独刷新的行情 store 只覆盖其显示价格。

增加对应 Python／TypeScript DTO 和类型化客户端方法。仅定义 schema 而没有接入实际字典响应不算完成；集成测试必须检查序列化后的真实载荷。

## 估值与最低价批次合并

- 通过现有辅助函数保留数量 4 位、价格 6 位、金额 4 位小数和 ROUND_HALF_UP。使用 Decimal 计算比率，按百分数单位序列化到 6 位小数，界面格式化另行处理。
- 持仓浮盈使用剩余数量／成本，成本含已分摊买入费用，不含预计未来卖出费用。收益率分母必须大于零，否则百分比为 null。
- 最低价候选批次必须属于同一账户、证券、策略，买入交易为 ACTIVE BUY、批次为 OPEN，且剩余数量大于零。
- 找出候选批次中最小的原始 `buy_trade.price`，纳入这个成交价下的**全部**候选批次。不按最早买入或其他时间规则从中挑一批。
- 合计所选批次的剩余数量与成本。`合计浮盈 = 最新价 × 合计剩余数量 − 合计剩余成本`；`合计浮盈率 = 合计浮盈 ÷ 合计剩余成本 × 100`。不得平均各批次浮盈率，也不得按含费单位成本决定最低买入价。
- 返回共同买入价 purchasePrice、批次数 lotCount、合计剩余数量／成本及合计浮盈金额／百分比。缺少报价时保留组信息，浮盈为 null；没有符合条件的批次时组为 null。
- 部分卖出只减少相应批次的剩余贡献。同价的一批结清后，只要该价位还有符合条件的批次，就保持原最低价；旧价位已无符合条件的批次时才换到下一最低价。
- 即使启用 includeClosed，也不能把已结清批次纳入当前持仓估值。保留现有已实现收益和策略回本价行为。
- 账户浮盈必须覆盖全部未结清持仓：全部有报价且有效为 fresh；全部有报价但至少一项过期为 stale；部分缺失为 partial；全部缺失为 unavailable；无未结清持仓为 empty。partial／unavailable 时，账户未实现收益金额和百分比为 null，并说明覆盖情况，不能显示误导性小计。empty 时金额为零、百分比为 null。
- 过期报价可用于明确标注过期的参考估值，但不得触发依赖有效行情的新目标提醒。行情失败不得修改账务余额。

## 前端生命周期与展示

- 增加类型化 `api.dashboard` 和统一首页加载动作，将整个估值响应一起更新到首页状态中。保持其他页面既有 ledger／quotes store 接口兼容。Vue 不重新计算浮盈。
- 挂载时先加载 cache，再请求 auto。刷新期间保留上一份一致结果，当前请求结束后才根据服务器 pollAfterSeconds 安排下一次计时。
- 手动刷新与在途请求串行执行或复用已有请求。document.hidden 时暂停自动请求，恢复可见时继续；卸载时清理计时器和监听器。通过请求代次或取消机制，防止交易、导航、重载后的旧响应覆盖新状态。
- 快速买入／卖出成功后，沿同一估值路径重载，并传入当前 includeClosed。切换 includeClosed 不能只独立覆盖估值结果的一部分。
- 首页 HTTP 请求失败时保持账务操作可用，保留最后有效的一致结果，并明确将其标为不可用或过期参考估值。
- 保留当前页面结构、导航、现金流卡片、快速记账和自选股部分。持仓表扩展当前价／当日涨幅、持仓浮盈金额／百分比、最低价批次合计浮盈金额／百分比。
- 使用紧凑说明展示最低价组的共同买入价、批次数及合计剩余数量，文案应明确合并该最低价下所有批次。
- 沿用现有 Element Plus 样式和涨跌配色偏好，使用文字说明过期／不可用状态，显示币种、报价时间或明确标识的获取时间。界面通常保留 2 位小数，分别处理 null 和零。
- 汇总需显示覆盖／状态信息，说明未扣预计卖出费用。不新增账户导航，不重设计整页。

## 兼容性与回退

- 预计不需要数据库迁移：新指标由现有交易、批次和缓存字段派生。
- 保留默认账户行为、账户／币种／策略隔离、美股账务，以及交易／配对／作废语义。本任务不增加美股实时行情。
- 仅在需要时更新 README／Compose，说明真实／模拟配置、轮询与上游延迟的区别、成本口径和离线行为。不扩展 SDK 依赖。
- 运行时可设置 `QUOTE_PROVIDER=mock` 并重启，回退到离线模式；现有账务事实和缓存参考价格保留。回退读取接口或界面不需要迁移账务数据。
- 公开行情的访问与新鲜度不作保证。规划时 SH/SZ 探测成功，不代表部署环境一定可达，也不代表已验证 BJ 实时行情。沿用现有工作日／已配置节假日和单工作进程限制。

## 最终评审条件

已纳入用户关于“最低价下所有批次合并”的澄清。本文档供最终评审使用；用户后续明确批准最终方案前，不运行 task.py start，也不修改产品代码。本任务说明文档使用中文，技术标识保留原文。
