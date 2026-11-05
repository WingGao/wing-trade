# M0 账本 REST API (m0-ledger-api) — PRD

## Goal

基于 `11-05-m0-ledger-domain-db` 的领域与数据库层，构建符合 Spec §8 契约的 FastAPI `/api/v1` REST 接口，提供证券搜索、自选股管理、买卖成交录入、FIFO/手动卖出配对预览与原子提交、交易撤销、持仓/批次/周期/止盈目标查询与配置、统计汇总以及健康检查能力。

## Confirmed Facts

- 规范依据：[docs/feat/ashare_personal_trading_spec.md](../../../../docs/feat/ashare_personal_trading_spec.md) §8, §11, §12, §14.2。
- 前置依赖：必须先完成 [11-05-m0-ledger-domain-db](../11-05-m0-ledger-domain-db/prd.md)。
- 统一前缀：`/api/v1`；所有错误统一返回 `{"error": {"code": "<ERROR_CODE>", "message": "<human_message>", "details": {...}}}`。
- 金额与数量字段在 JSON 契约中统一采用字符串序列化（如 `"600.0000"`, `"11.000000"`）或支持从十进制字符串/数字精确解析为 `Decimal`，严禁 JSON 浮点精度丢失。

## Requirements

- **R-01 基础证券与自选股接口**：
  - `GET /api/v1/securities?query=...`（支持按代码、名称检索及新增基础证券种子数据）。
  - `GET / POST /api/v1/watchlist`、`DELETE /api/v1/watchlist/{securityId}`（支持添加、移除、排序自选股）。
- **R-02 交易流水、买入、卖出预览与原子提交接口**：
  - `GET /api/v1/trades?accountId=&securityId=&includeVoided=false`。
  - `POST /api/v1/trades/buy`：支持 `client_request_id` 幂等提交，创建 `BUY` Trade 与 `Lot`。
  - `POST /api/v1/trades/sell/preview`：输入卖出数量、单价、费用及可选手动配对，返回默认 FIFO 推荐或手动配对下的分摊成本、净收入与预计已实现收益预览（不落库）。
  - `POST /api/v1/trades/sell`：在 `BEGIN IMMEDIATE` 单写事务内锁定 Lot，校验余额并原子落库 `SELL` Trade 与全部 `TradeMatch`。
  - `POST /api/v1/trades/{id}/void`：带原因撤销错误成交并生成 `audit_events`。
- **R-03 持仓、批次、配对、周期与汇总分析接口**：
  - `GET /api/v1/positions?accountId=&includeClosed=false`：聚合当前持仓、加权成本、毛浮盈（若行情缓存存在）、策略回本参考价。
  - `GET /api/v1/securities/{id}/lots?includeClosed=false`：查询个股批次明细及已结清汇总（“已结清 X 批、已实现收益 ¥Y”）。
  - `GET /api/v1/lots/{id}/matches`：查询某买入批次的卖出抵消记录。
  - `GET /api/v1/cycles?securityId=&includeClosed=false`：查询交易周期及归档汇总。
  - `GET /api/v1/analytics/summary?accountId=`：汇总累计买入现金流、卖出回款、已实现收益、未实现收益。
- **R-04 分段止盈目标接口**：
  - `GET / POST /api/v1/lots/{id}/targets`、`PATCH / DELETE /api/v1/targets/{id}`。
- **R-05 可观测性与健康检查**：
  - 结构化日志中间件（记录 `request_id`、耗时、错误分类）；提供 `/health` 与 `/ready` 端点。

## Acceptance Criteria

- [ ] 全部 Spec §8 核心账本端点可通过 FastAPI `TestClient` / `httpx.AsyncClient` 走通完整交易生命周期。
- [ ] 相同 `(account_id, client_request_id)` 重复调用 `POST /api/v1/trades/buy` 或 `/sell` 返回原交易结果（HTTP 200/201），数据库记录不增加。
- [ ] 并发卖出争用同一 Lot 的集成测试中，仅合法组合成功，超额请求返回 `409 Conflict` / `422 Unprocessable Entity` 且错误码为 `INSUFFICIENT_LOT_QTY`。
- [ ] 切换 `includeClosed=false/true` 时，返回的已实现总收益、未实现浮盈、累计现金流完全一致。

## Out of Scope

- AKShare 实时外部行情采集与调度（归属 `11-05-m1-market-quote`）。
- CSV 导入与 WebSocket/SSE 推送（归属 V1）。
