# 长桥 OpenAPI Python SDK 调研笔记

## 1. 包与 Python 版本兼容性

- 官方推荐 Python 包：`longbridge`（PyPI 5.4.0+ 已发布 `cp314` 预编译二进制 wheel，兼容项目当前 `backend/.venv` 的 Python 3.14 环境，无需本地 Rust 编译工具链）。
- 开发与离线测试边界：
  - SDK 导入应保持延迟加载（lazy import）或依赖注入，确保 `QUOTE_PROVIDER=mock` / `eastmoney` 及所有单元测试在不依赖公网、甚至未安装或 mock SDK 时仍可正常运行。

## 2. 认证方式（API Key 静态 Token 模式）

用户已确认采用长桥开发者后台生成的静态三元组：

```python
from longbridge.openapi import Config, QuoteContext

config = Config.from_apikey(
    app_key="...",
    app_secret="...",
    access_token="...",
    # 可选参数：
    # enable_overnight=True,
    # http_url="https://openapi.longbridge.cn",
    # quote_ws_url="wss://openapi-quote.longbridge.cn",
)
ctx = QuoteContext(config)
```

- 环境变量标准名：`LONGBRIDGE_APP_KEY`、`LONGBRIDGE_APP_SECRET`、`LONGBRIDGE_ACCESS_TOKEN`、`LONGBRIDGE_ENABLE_OVERNIGHT`。
- 注意：静态 `access_token` 存在有效期（通常 90 天），过期或失效时需在日志中给出明确告警并平滑回退，不得影响账务主流程。

## 3. 代码映射与市场覆盖

| 项目内部规范代码 | 长桥代码格式 | 支持说明 |
| :--- | :--- | :--- |
| `SH:600519` | `600519.SH` | 上交所股票/ETF，LV1 实时推送（需注意大陆合规网络出口以获取实时而非 15 分钟延时） |
| `SZ:000001` | `000001.SZ` | 深交所股票/ETF，LV1 实时推送 |
| `US:AAPL` | `AAPL.US` | 美股股票/ETF，默认覆盖盘前/常规/盘后；开启 `enable_overnight` 可接收夜盘 |
| `BJ:xxxxxx` | 不走长桥主推 | 北交所回退现有 `EastmoneyQuoteProvider` |
| `OF:xxxxxx` | 不支持 | 场外基金官方净值保留 `EastmoneyFundQuoteProvider`；已绑定 `proxy_symbol` 时由内存中心随参考标的推流即时派生估算净值 |

## 4. `SecurityQuote` 基线与 `PushQuote` 增量推送

- **关键特性**：`PushQuote` 只携带变动字段，**不包含 `prev_close`（昨收）**。
- **正确接入流程**：
  1. 订阅新增标的时，先调用 `ctx.quote(symbols)`（单次上限 500 只）获取 `SecurityQuote`：
     - `last_done`（`Decimal`）
     - `prev_close`（`Decimal`）
     - `timestamp`
     - `pre_market_quote` / `post_market_quote` / `overnight_quote`（美股扩展时段基线）
  2. 注册回调 `ctx.set_on_quote(on_quote)` 后调用 `ctx.subscribe(symbols, [SubType.Quote], is_first_push=True)`。
  3. 回调 `on_quote(symbol: str, event: PushQuote)` 收到 `last_done`、`timestamp`、`trade_session`、`trade_status` 等字段时：
     - 结合内存基线 `prev_close`（美股盘后优先使用当日常规收盘价，回退 `prev_close`）用 `Decimal` 精确计算 `change_pct`；
     - 忽略非正数或缺失价格；
     - 将 `trade_session` 映射为项目 `MarketSessionPhase`（`pre` / `regular` / `post` / `closed`）。

## 5. 线程模型与配额限制

- **线程安全桥接**：`longbridge` Python SDK 底层为 Rust 异步运行时，`on_quote` 回调在非 Python 主线程中触发，必须使用 `loop.call_soon_threadsafe(...)` 投递到 FastAPI 主事件循环，严禁在回调线程直接操作 SQLAlchemy Session 或 `await`。
- **配额上限**：
  - 单账户最多 1 条活跃行情长连接；
  - 单连接同时订阅上限 **500 只标的**；
  - 行情 API 请求频率上限 10 次/秒，并发不超过 5。
- **相关规范文档**：
  - `.trellis/spec/backend/market-data-guidelines.md`
  - `.trellis/spec/backend/quality-guidelines.md`
  - `.trellis/spec/frontend/active-position-dashboard.md`
  - `.trellis/spec/frontend/portfolio-detail-guidelines.md`
  - `.trellis/spec/frontend/state-management.md`
