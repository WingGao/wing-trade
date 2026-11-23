# 调研笔记：场外开放式基金适配

## 1. 现有代码库能力摸底

- **数据库与领域精度**：
  - `backend/app/models/ledger.py`：`Security` 已有 `security_type: String(32)` 字段（默认 `"STOCK"`）。
  - `backend/app/domain/precision.py`：`QTY_SCALE = 0.0001`、`PRICE_SCALE = 0.000001`、`AMOUNT_SCALE = 0.0001`，后端天然支持场外基金的小数份额与 4 位单位净值。
- **市场与币种**：
  - `backend/app/domain/markets.py` 原有市场仅 `SH`、`SZ`、`BJ`、`US`，扩展 `OF -> CNY` 即可复用现有 `CNY` 账户校验。
- **行情与查名现状**：
  - `backend/app/market_data/provider.py` 的 `EastmoneyQuoteProvider` 面向 `SH`/`SZ`/`BJ` 场内证券，并在 `.trellis/spec/backend/market-data-guidelines.md` 中有明确契约约束，不宜直接扩大其职责，应通过独立 `EastmoneyFundQuoteProvider` + `CompositeQuoteProvider` 组合扩展。
  - `backend/app/market_data/security_info.py` 使用东方财富 `suggest` 接口查名，原有过滤条件仅放行 `MktNum in {"0", "1"}` 和美股，需在保留场内 ETF 归类前提下放行场外基金。

## 2. 东方财富公开场外基金已公布净值接口

- **接口地址**：`https://api.fund.eastmoney.com/f10/lsjz`
- **请求参数**：`fundCode=<6位代码>&pageIndex=1&pageSize=<条数>`
- **必要请求头**：`Referer: https://fundf10.eastmoney.com/`
- **响应结构**：纯 JSON 对象，`Data.LSJZList` 为按净值日期倒序的数组，关键字段：
  - `FSRQ`：净值日期（`YYYY-MM-DD`）
  - `DWJZ`：已公布单位净值（字符串十进制）
  - `JZZZL`：日增长率（百分比字符串，可能为空）
- **使用方式**：
  - 最新行情快照取 `pageSize=2`（最新净值 + 前一日净值），避免引入盘中估值噪音。
  - 详情页近期净值曲线取 `pageSize=30`，按 `FSRQ` 升序排列后直接复用 `IntradayChart.vue`（通过 `seriesKind="nav"` 区分坐标轴格式）。

## 3. 相关规范文档索引

- `.trellis/spec/backend/market-data-guidelines.md`
- `.trellis/spec/backend/quality-guidelines.md`
- `.trellis/spec/frontend/quality-guidelines.md`
