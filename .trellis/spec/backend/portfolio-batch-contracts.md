# 持仓批次、重建与三级止盈契约

## 1. 适用范围

修改个股批次、卖出匹配、派生账务重建、止盈继承或证券行情接口时必须遵守本契约。金额计算只在后端使用 Decimal，前端保留字符串。

## 2. 签名

- `MatchingService.auto_preview(..., method=MatchingMethod.EQUAL)`：最近优先最小覆盖集合内按容量等量分配。
- `RecalculationService(session, actor=None).rebuild(account_id, security_id, strategy_id)`：调用方拥有单一写事务。
- `ProfitSettingsService.effective_target(session, lot)`：只读解析批次、个股、全局优先级。
- `GET/PUT /api/v1/profit-settings`、`GET/PUT /api/v1/securities/{id}/profit-settings`。
- `POST /api/v1/securities/{id}/recalculate?accountId=&strategyId=`。
- `GET /api/v1/securities/lookup?query=`、`GET /api/v1/securities/{id}/intraday`。
- 模型新增 `ProfitSetting`，迁移 `0002_profit_settings`；全局唯一键 `GLOBAL`，个股唯一键 `SECURITY:{securityId}`。

## 3. 数据契约

- 止盈 `targetReturnPct` 是小数率：`0.1` 表示10%；估值 `unrealizedReturnPct` 是百分数：`10` 表示10%。不可混用。
- 设置 PUT 的 `targetReturnPct:null` 删除本层覆盖；GET 返回设置或 null。只对用户明确保存的覆盖持久化；取消的批次目标不占计划量且不阻止继承。
- lot 新增 `unrealizedPnl`、`unrealizedReturnPct`、`effectiveTarget`；继承投影包含 `source=LOT|SECURITY|GLOBAL`、收益率、口径、估计价格和提醒开关。已结清批次浮盈和继承目标为空。
- lots 响应有 `currency` 与 `quote`，已实现盈亏统计所有有效卖出匹配，包括仍持有批次的部分卖出。Dashboard 的 `summary.marketValue` 与当前估值同源，缺价时为空，空仓为零。
- 开启 `includeClosed` 时 lots 响应的 `closedTradeDetails` 返回同一账户/证券/策略内全部有效已结清批次的原始买入及每笔有效卖出配对，关闭时为空数组。每行含 `lotId`、`tradeId`、`side`、`executedAt`、`qty`、`originalTradeQty`、`price`、`cashAmount`、`realizedPnl`；时间UTC、时间倒序，数值十进制字符串。买入现金额是含费原始成本，买入收益为空；卖出数量是本批匹配量、现金额是本批分摊净回款、收益是该匹配已实现收益，`originalTradeQty` 只辅助注明原整笔量。不能把整笔卖出重复归到每个批次，也不能用含费单位成本反推原成交价。读取不写库，作废交易不作为有效成交返回。
- EQUAL：先按最近买入选最小覆盖集合，再按整数尽量均分；容量不足的先扣完，其余继续平分，余股按最近优先。旧小数持仓保留0.0001精度守恒，FIFO/LIFO/MANUAL兼容。
- 重建只清理指定账户/证券/策略的派生匹配、批次与周期，按有效原交易重放当前 EQUAL；不生成新 Trade、不删审计。保留旧 lot ID 及明确目标，周期锚定事实产生稳定 ID，失败整笔回滚。
- 查名每次显式请求访问外部服务，返回候选及可选本地 ID；不隐式落库。外部失败保留本地结果并返回 error。
- 日内序列只接受真实当日数据；美股上游分钟时间为北京时间，交易日按纽约判断。日内价写共享 QuoteCache，后续 lots 使用同价估值。失败保留同日缓存并标 stale；成功与失败都限频，缓存有界。

## 4. 校验与错误矩阵

| 条件 | 结果 |
|---|---|
| EQUAL 卖出量超过可用量 | INSUFFICIENT_LOT_QTY，事务不变 |
| 重放遇到无买入的卖出 | 整次回滚，原派生数据完整保留 |
| 设置非有限率、非法口径或负收益目标 | VALIDATION_ERROR 422，可序列化上下文 |
| 外部名称服务失败 | 200，候选保留本地库，error 明确失败 |
| 首次日内获取失败 | 200，points为空、quote为空、isStale=true |
| 有同日缓存后失败 | 同日真实曲线保留，isStale=true，不重写获取时间 |
| 外部返回其他证券或前交易日曲线 | 不覆盖该证券当日行情 |
| 关闭显示已结清 | `closedTradeDetails=[]`，不额外返回结清买卖 |
| 一笔卖出配对多个批次 | 每批只返回本批匹配量与分摊回款/收益，整笔量单独注明 |

## 5. 正常、边界与错误示例

- 正常：最近两批各100股，卖150股，等量75+75；差价包含在个股已实现盈亏中。
- 边界：新批2、旧批10，卖6，容量约束分成2+4；总量仍是6。
- 明细：买入100股，先卖40股再卖60股结清，显示1条买入及2条卖出；跨批卖出按各批分摊显示，不复制整笔金额。
- 错误：个股 GET 为每个继承批次新建 ProfitTarget；按文本列 SQL `remaining_qty > 0`；重建调用 buy/sell 生成重复事实。

## 6. 必须测试

单测断言等量、余数、容量、范围和数量/金额守恒；集成测试断言预览/确认一致、继承GET零写入、取消目标额度、部分卖出收益、重建重复稳定及跨账户/证券隔离与失败回滚。行情使用固定 transport 覆盖代码市场校验、真实名称、缺价、过期、跨日、美股跨北京时间午夜、有界缓存和失败限频。迁移只能用临时库 upgrade/downgrade，禁止删除清空本地 ledger.db。

已结清明细还须断言1买多卖、多闭仓周期、跨批部分匹配的准确原始成交价/费用分摊、作废排除、账户/证券/策略隔离、开关为空与读取零写入。

## 7. 错误与正确写法

```python
# 错误：通过新交易重建，改变事实与幂等键。
# TradeLedgerService(session).buy(...)

# 正确：API begin_write 之后调用派生重放，错误由 API rollback。
result = RecalculationService(session).rebuild(
    account_id=account_id, security_id=security_id, strategy_id=strategy_id
)
```
