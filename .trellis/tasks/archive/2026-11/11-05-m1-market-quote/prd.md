# M1 行情采集与缓存 (m1-market-quote) — PRD

## Goal

实现可插拔的 `QuoteProvider` 抽象、AKShare 行情适配器、服务端共享行情缓存（`quote_cache`）、A 股交易日历与时段判断、退避熔断机制以及 `/api/v1/quotes` 接口，并联动更新未结清批次分段止盈目标的提醒状态（`TRIGGERED`），同时保证行情异常或离线时绝不影响账本核心功能。

## Confirmed Facts

- 规范依据：[docs/feat/ashare_personal_trading_spec.md](../../../../docs/feat/ashare_personal_trading_spec.md) §4.4, §8, §9, §12.1, §14.2。
- 前置依赖：必须先完成 [11-05-m0-ledger-api](../11-05-m0-ledger-api/prd.md)。
- 采集范围：仅采集自选股（`watchlist_items`）与当前有未结清持仓的证券（`open lots`），所有客户端共享同一服务端缓存，浏览器刷新不直接穿透拉取上游。

## Requirements

- **R-01 `QuoteProvider` 抽象与 AKShare 适配器**：
  - 定义 `get_quotes(symbols: Sequence[str]) -> dict[str, QuoteSnapshot]` 与 `health() -> ProviderHealth`。
  - 实现 `AKShareQuoteProvider`（同步 SDK 置于线程池执行）及用于离线/测试环境的 `MockQuoteProvider` / `FallbackQuoteProvider`。
- **R-02 服务端缓存、过期判定与熔断降级**：
  - 维护 `last_price`, `prev_close`, `change_pct`, `source_time`, `fetched_at`, `provider`, `is_stale`。
  - 交易时段（沪深交易日 09:15–11:30、13:00–15:00）默认 10 秒轮询；非交易时段降低采集频率并保留收盘有效报价。
  - 上游超时/报错时采用指数退避与短暂熔断，保留最近有效价格并标记 `is_stale=True`，严禁将价格重置为 `0`。
- **R-03 止盈目标状态联动评估**：
  - 当获取到**非过期**（`is_stale=False`）的有效报价且 `last_price >= target.target_price_estimate`、`alert_enabled=True` 时，将 `ACTIVE` 状态的 `ProfitTarget` 更新为 `TRIGGERED` 并记录 `triggered_at`（支持冷却期防重，且绝不自动生成卖出交易）。
- **R-04 行情查询与手动刷新接口**：
  - `GET /api/v1/quotes?symbols=SH:600519,SZ:002594&refresh=false`（`refresh=true` 受服务端最小间隔节流保护）。

## Acceptance Criteria

- [ ] 有效报价到达止盈阈值时，`ProfitTarget` 状态转为 `TRIGGERED`；当报价标记为 `is_stale=True` 时，不触发新的目标提醒。
- [ ] 模拟上游行情源抛出超时或网络异常时，`/api/v1/quotes` 返回缓存快照且 `is_stale=True`（或明确标记暂无数据），`/api/v1/positions` 与 `/api/v1/trades` 等账本接口正常响应。
- [ ] 多并发请求 `/api/v1/quotes` 共享服务端缓存，不会对上游发起重复并发请求。

## Out of Scope

- WebSocket / SSE 主动推送、K 线历史接口（归属 V1）。
