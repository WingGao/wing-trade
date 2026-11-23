# 美股与实时行情技术调研

## 1. 代码库现状核查

### 1.1 后端行情与日历层
- **市场与币种映射**（`backend/app/domain/markets.py`）：已定义 `MARKET_CURRENCY["US"] = "USD"`，账户与证券币种隔离校验（`validate_account_security`）已就位。
- **交易日历**（`backend/app/market_data/calendar.py`）：
  - 已有 `USTradingCalendar`，时区为 `America/New_York`（离线无 `tzdata` 时回退固定偏移），但目前仅定义了常规交易时段 `09:30 - 16:00`，**尚未支持盘前（04:00–09:30）与盘后（16:00–20:00）时段区分**。
- **批量行情源**（`backend/app/market_data/provider.py` & `factory.py`）：
  - `create_quote_provider()` 在 `quote_provider="eastmoney"` 时返回 `CompositeQuoteProvider([EastmoneyQuoteProvider, EastmoneyFundQuoteProvider])`。
  - `EastmoneyQuoteProvider` 仅支持 `SH/SZ/BJ` 6 位数字代码，直接过滤 `US:*`，导致美股无法批量拉取行情。
  - `CompositeQuoteProvider` 已支持按 `supports_symbol()` 路由并具备单子源失败容错，非常适合直接扩展 `EastmoneyUSQuoteProvider`。
- **证券查名与分时**（`backend/app/market_data/security_info.py`）：
  - `search()` 已支持通过东方财富 `suggest` 接口检索美股（`MktNum in {"105", "106", "107"}` 或 `Classify in {"US", "USSTOCK"}`）。
  - `intraday()` 拉取美股分时存在三个明确缺陷：
    1. 东财美股交易所编号映射 `_us_markets` 仅保存在 `SecurityInfoProvider` 实例内 LRU，未与批量行情源共享。
    2. `trends2/get` 固定传 `iscr=0`，未开启美股盘前/盘后扩展时段。
    3. 解析 `trends` 时间字符串时写死了 `tzinfo=self.calendar.timezone`（上海时区），未按美股目标日历/实际返回时区做稳健归属，跨北京时间午夜时容易截断或误过滤。
- **Dashboard 交易时段判断**（`backend/app/services/dashboard.py`）：
  - `DashboardService.build()` 第 54 行固定调用 `self.calendar.is_session_open(current)`（A 股日历），导致 USD 账户在美股盘前/盘中/盘后（北京时间晚间至次日清晨）被误判为休市并退化为 60 秒轮询。
- **账务精度与账户接口**（`backend/app/models/ledger.py` & `backend/app/api/v1/router.py`）：
  - `Trade.qty`、`Lot.original_qty`、`Lot.remaining_qty`、`TradeMatch.matched_qty` 均已采用 `ExactDecimal(4)`，底层账务天然支持美股碎股（最多 4 位小数）。
  - `GET /api/v1/accounts`、`POST /api/v1/accounts`（支持 `CNY`/`USD`）、`GET /api/v1/trades?accountId=...`、`GET /api/v1/analytics/summary?accountId=...`、`GET /api/v1/cycles?accountId=...` 后端均已实现。

### 1.2 前端多账户、多币种与美股交互现状
- **导航与品牌**（`frontend/src/App.vue`）：侧边栏标题仍硬编码为“A 股交易账本”；缺少全局账户切换器与新建账户入口。
- **总览页**（`frontend/src/pages/DashboardPage.vue`）：
  - 虽然加载了 `accounts` 用于快速记账，但顶部缺少账户切换器，`loadValuation` 默认只加载 CNY 账户，无法直接切换到 USD 账户总览。
- **交易流水页**（`frontend/src/pages/TradesPage.vue`）：
  - `api.trades()` 未传 `accountId`；价格和费用列未带币种符号（`$` / `¥`）。
- **复盘分析页**（`frontend/src/pages/AnalyticsPage.vue`）：
  - `api.summary()` 与 `api.cycles()` 未传 `accountId`；金额前缀硬编码为 `¥`；交易周期表直接展示原始 `securityId` UUID。
- **碎股与手续费**（`frontend/src/utils/format.ts`、`BuyTradeDialog.vue`、`SellMatchDrawer.vue`、`LotTable.vue`）：
  - `formatQuantity(value, isFund)` 对非基金一律四舍五入取整（`getNumberFormatter(0)`），会错误截断美股碎股小数（如 `0.25` 股变成 `0` 股）。
  - `BuyTradeDialog.vue` 非基金输入强制 `inputmode="numeric"`，且仅展示了“佣金”一项；`SellMatchDrawer.vue` 缺少卖出费用输入项。

## 2. 东方财富公开美股接口调研

### 2.1 交易所编号映射
东方财富美股 `secid` 格式为 `{market_num}.{SYMBOL}`：
- `105`：NASDAQ（如 `105.AAPL`、`105.TSLA`）
- `106`：NYSE（如 `106.BABA`、`106.KO`）
- `107`：NYSE American / AMEX / Arca ETF（如 `107.SPY`、`107.QQQ` 部分标的可在 105 或 107）

通过 `https://searchapi.eastmoney.com/api/suggest/get` 返回的 `MktNum` 可精确获得每只美股对应的市场编号；若查名未命中，可按 `105 -> 106 -> 107` 顺序探测一次并缓存。

### 2.2 批量快照与盘前/盘后字段
- **批量接口**：`https://push2.eastmoney.com/api/qt/ulist.np/get`
  - 请求参数：`fltt=2&invt=2&secids=105.AAPL,106.BABA&fields=f12,f13,f14,f2,f3,f18,f124,f251,f253,f292`
  - 常规字段：`f2`（最新价）、`f3`（涨跌幅 %）、`f18`（昨收）、`f124`（Unix 秒时间戳）。
  - 盘前/盘后扩展字段：`f251`（盘前/盘后最新价）、`f253`（盘前/盘后涨跌幅 %）、`f292`（交易状态/扩展时段标识）。
  - 兼容性策略：在美股盘前（`04:00–09:30 ET`）或盘后（`16:00–20:00 ET`）时段，若上游返回有效的正数 `f251`（盘前/盘后价），优先将其作为当前活跃快照价格（`last_price`）并标注 `market_session="pre" | "post"`，同时保留 `prev_close`（若 `f253` 有效则采用之，否则按 `last_price` 与基准价计算）；若 `f251` 缺失或为 `-`，则平滑回退到 `f2` 常规最新价，绝不伪造价格。
- **分时接口**：`https://push2his.eastmoney.com/api/qt/stock/trends2/get`
  - 对美股传入 `iscr=1` 以请求包含盘前/盘后的扩展时段分钟点；同时兼容 `trends` 返回的北京时间字符串，统一转换为 UTC 后再按 `America/New_York` 判断是否属于当前美股交易日（解决跨北京时间 00:00 的日期归属问题）。

## 3. 相关规范文件索引
- `.trellis/spec/backend/market-data-guidelines.md`
- `.trellis/spec/backend/portfolio-batch-contracts.md`
- `.trellis/spec/backend/quality-guidelines.md`
- `.trellis/spec/frontend/active-position-dashboard.md`
- `.trellis/spec/frontend/portfolio-detail-guidelines.md`
- `.trellis/spec/frontend/mobile-high-density.md`
