# 技术设计

## 改动边界

账务在 services/domain，第三方请求在 market_data，HTTP 验证和事务在 api，Vue 只展示投影，沿用既有组件样式。

## 接口约定

- Dashboard summary 新增 `marketValue: string|null`，与现有 valuationStatus 同步。
- `GET /securities/lookup?query=` 返回 `{securities: SecurityCandidate[], error: string|null}`，候选包含 market/symbol/name/currency、可选 id；只查不写，选择外部候选后复用 POST /securities。
- `GET /securities/{id}/intraday` 返回 `{symbol,currency,tradingDate,points:[{time,price}],quote:Quote|null,provider,fetchedAt,isStale,error:string|null}`。真实时序，3 秒轮询，失败保留同日缓存且明确过期。
- lots 原响应新增 `quote:Quote|null`、`currency`；每个 lot 新增 `unrealizedPnl:string|null`、`unrealizedReturnPct:string|null`、`effectiveTarget:EffectiveTarget|null`。已实现收益包含所有有效卖出匹配。
- `EffectiveTarget` 包含 `source:'LOT'|'SECURITY'|'GLOBAL'`、`targetReturnPct`、`calculationBasis`、`targetPriceEstimate`、`alertEnabled`、可选 id/plannedSellQty，只读投影、不写继承副本。
- `GET /profit-settings`、`PUT /profit-settings` 全局；`GET /securities/{id}/profit-settings`、`PUT /securities/{id}/profit-settings` 个股。GET 返回设置或 null，PUT 接受 `{targetReturnPct:string|null,calculationBasis:'NET'|'GROSS',alertEnabled:boolean}`，null 清本层覆盖。批次沿用 create/update/delete targets，取消记录不占计划数量。
- `POST /securities/{id}/recalculate?accountId=&strategyId=` 返回 `{rebuiltLots:number,rebuiltMatches:number}`，单事务重建并追加审计。
- MatchingMethod 增加 `EQUAL`，工作台显式使用，保留原策略；预览确认复用同一分配逻辑。
- trades 可扩展 market/symbol/name，前端可通过证券目录兼容回退。
- R11 补充：lots 响应新增 `closedTradeDetails: SettledTradeDetail[]`，仅 `includeClosed=true` 返回结清批次买入及有效卖出，关闭时为空。每条包含 `lotId`、`tradeId`、`side:BUY|SELL`、`executedAt`、`qty`、`originalTradeQty`、`price`、`cashAmount`、`realizedPnl:string|null`。买入 qty 为原始买入量、cashAmount 为原始含费成本；卖出 qty 为该批 matchedQty、cashAmount 为该批 allocatedSellNet、realizedPnl 为该匹配收益，originalTradeQty 为原始整笔成交量。所有金额/数量十进制字符串，时间规范UTC，按时间倒序确定性排序，排除作废交易。前端在原批次表下增加“已结清买卖明细”原生表格，开启即显示完整列表；不复制重算账务，不新增数据库结构，不向轮询添加额外逐批请求。

## 数据迁移与重建

新增全局/个股唯一止盈设置表和新 Alembic revision，批次继续现有 ProfitTarget。不改已应用迁移。

重建不得调用生成 Trade 的 buy/sell。保留买入事实→lot ID/targets 映射，先删匹配再删批次，按有效交易时间重放（同刻买入优先），只改指定范围，按当前 EQUAL 策略重算匹配，修正 cycle 和 trade.cycle_id。原 Trade ID/金额/状态/幂等字段及审计历史保留。失败事务回滚。

## 行情

外部信息与东方财富当日 trends 使用可注入 transport、超时和响应上限；Decimal 解析价格，异步路由线程执行 HTTP。缓存有界且按交易日隔离。支持沪深北和美股；美股先查上游105/106/107市场，分钟时间实际为北京时间，转换后按纽约交易日筛选，保留北京时间跨午夜的整段时序。其他市场明确返回不可用。测试均离线 mock。

## 风险及验证

等量、容量不足、余数确定性；预览确认一致；SQLite 文本 Decimal 禁止 SQL 数值比较；时间统一 UTC；重建外键/ORM 缓存、范围隔离、幂等和回滚；继承 GET 零写入。迁移只用临时库测试，真实库不自动迁移或重建。
