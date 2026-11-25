# 技术设计：美股 20 日振幅接入长桥日 K 主源与腾讯备源修复

## 1. 变更边界

本任务仅涉及后端行情适配层与路由装配，不涉及数据库迁移与前端变更：

| 文件 | 变更内容 | 必要性 |
|---|---|---|
| `backend/app/market_data/longbridge_adapter.py` | 在 `LongbridgeQuoteProvider` 上新增 `get_daily_candlesticks(symbol, count)` 方法 | 复用既有 `QuoteContext`、锁与 `to_longbridge_symbol`，避免重复建连与凭据散落 |
| `backend/app/market_data/amplitude.py` | 1) `DailyAmplitudeProvider` 支持可选注入长桥日 K 拉取器并作为主源；2) 修复 `_fetch_tencent_us_kline_bars` 后缀映射/探测与占位行过滤 | 解决美股无 20 日振幅及失真计算的根因 |
| `backend/app/api/v1/router.py` | 当 `quote_collector.provider` 包含 `LongbridgeQuoteProvider` 时，将其注入 `DailyAmplitudeProvider` | 让已启用长桥的环境自动享受长桥日 K 主源，零额外配置 |
| `backend/tests/unit/test_daily_amplitude.py` | 新增覆盖长桥主源、长桥失败回退、腾讯 `.OQ`/`.N`/`.AM` 映射与探测、跨年占位行防护的单元测试 | 验证所有新增与修复路径，离线可复现 |
| `.trellis/spec/backend/market-data-guidelines.md` | 同步更新“个股近 20 个已收盘交易日平均振幅”契约说明 | 遵循 spec-sync 规范，固化长桥日 K 主源与腾讯后缀约定 |

---

## 2. 长桥日 K 主源设计

### 2.1 `LongbridgeQuoteProvider.get_daily_candlesticks`
在 `backend/app/market_data/longbridge_adapter.py` 中为 `LongbridgeQuoteProvider` 新增同步方法：

```python
def get_daily_candlesticks(
    self,
    symbol: str,
    *,
    count: int = 25,
) -> list[Any]:
```

- **代码映射**：调用 `to_longbridge_symbol(symbol)`；若返回 `None`（如 `BJ`、`OF`），直接返回 `[]`，不发起网络调用。
- **SDK 调用**：延迟导入 `longbridge.openapi` 中的 `Period`、`AdjustType`、`TradeSessions`，通过 `self._get_context()` 获取复用上下文，调用：
  ```python
  ctx.candlesticks(
      lb_symbol,
      Period.Day,
      count,
      AdjustType.ForwardAdjust,
      TradeSessions.Intraday,
  )
  ```
  - 使用 `AdjustType.ForwardAdjust`（前复权）：普通交易日 `(high - low) / prev_close` 与不复权一致，发生除权/拆股时可避免 `prev_close` 跳空失真。
  - 使用 `TradeSessions.Intraday`（常规交易时段）：避免美股盘前/盘后扩展时段极值拉偏日内常规振幅。
  - 兼容测试替身或旧版 SDK：若 `ctx.candlesticks` 签名不接受 5 个位置参数，回退 4 参数调用。
- **异常恢复**：与 `get_quotes` 一致，调用异常时在 `self._lock` 下重置 `self._ctx = None` 后向上抛出，由上层 `DailyAmplitudeProvider` 捕获并降级到备源。

### 2.2 在 `DailyAmplitudeProvider` 中解析长桥日 K
- `DailyAmplitudeProvider.__init__` 新增可选参数：
  ```python
  longbridge_kline_fetcher: Callable[[str, int], Sequence[Any]] | None = None
  ```
  （也可直接传入实现了 `get_daily_candlesticks` 的对象或可调用函数，保持简单解耦。）
- 新增 `_fetch_longbridge_kline_bars(canonical: str, *, market: str, limit: int) -> list[tuple[date, Decimal]]`：
  1. 调用 `longbridge_kline_fetcher(canonical, limit)` 获取 `Candlestick` 列表。
  2. 对每根 K 线解析 `timestamp`（通过 `_normalize_timestamp` 转为 UTC 后，美股按 `self.us_calendar.timezone`、A 股按 `self.a_calendar.timezone` 取交易自然日 `bar_date`）、`close`、`high`、`low`（使用 `Decimal` 解析，校验均为正数且 `high >= low`）。
  3. 按日期升序去重后，逐日计算 `(high - low) / prev_close * Decimal("100")`，并产出 `(bar_date, amplitude)` 列表。
- **路由顺序（`_fetch_bars_for_symbol`）**：
  1. 若配置了 `longbridge_kline_fetcher` 且市场属于长桥支持范围（`SH` / `SZ` / `US`）：优先拉取长桥日 K，成功且非空时返回 `(bars, "longbridge-kline")`；发生异常或返回空列表时记录 debug/warning 日志并继续执行原有回退链路。
  2. 原有回退链路：
     - A 股（`SH`/`SZ`/`BJ`）：东方财富日 K（`eastmoney-kline`）。
     - 美股（`US`）：**在长桥未启用或失败时**，按美股市场路由尝试腾讯美股日 K（带后缀）与东方财富日 K。考虑到东方财富 `push2his` 当前易断连且腾讯带后缀更稳定，美股备源顺序为：
       - 若已有 `USMarketResolver` 缓存的市场码（`105`/`106`/`107`）→ 直接映射腾讯后缀，或先尝试腾讯带后缀；
       - 实际上先尝试 **腾讯美股日 K（带交易所后缀）** 再回退 **东方财富美股日 K**（或东财先试但受限于断连会消耗一次 RTT）。考虑到单批 `batch_timeout_seconds = 4.0`，把更稳定的 **腾讯美股日 K（带后缀）** 放在东财之前（与 `factory.py` 中 `FallbackUSQuoteProvider(TencentUSQuoteProvider, EastmoneyUSQuoteProvider)` 的既定美股策略完全一致！），可以大幅缩短响应时间并避免被 4 秒超时截断。

---

## 3. 腾讯美股日 K 备源修复设计

### 3.1 交易所后缀映射与探测
腾讯 `https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get` 接口对美股历史 `qfqday` 必须携带交易所后缀：
- 纳斯达克：`105` → `.OQ`（如 `usAAPL.OQ`、`usNVDA.OQ`）
- 纽交所：`106` → `.N`（如 `usBABA.N`）
- 美交所 / NYSE Arca（ETF 常见，如 `VOO`、`SPY`）：`107` → `.AM`（如 `usVOO.AM`、`usSPY.AM`）

构造候选列表 `_tencent_us_symbol_candidates(code: str, resolved_market: str | None) -> tuple[str, ...]`:
- 定义映射 `_US_MARKET_TO_TENCENT_SUFFIX = {"105": ".OQ", "106": ".N", "107": ".AM"}` 与默认探测顺序 `(".OQ", ".N", ".AM")`。
- 若 `resolved_market` 已有明确映射（来自 `USMarketResolver.get_cached(code)`，无需先阻塞发起东财查名请求），将该后缀排在第一位，其余后缀作为备选依次探测。
- 仅当响应节点中包含 `qfqday` 且解析出有效振幅序列（或包含足够连续日 K）时即命中返回，同时将命中的交易所编号记回 `self._us_resolver.remember(code, upstream_market)` 供后续零探测复用。
- 若三个后缀均未命中，保留最后兼容回退（如无后缀 `us{code}` 仅在返回连续有效多日 K 时采信，兼容历史单测与特殊标的）。

### 3.2 跨年占位行与异常断档防护
实测腾讯在未命中正确后缀时，会在 `day` 中返回 `2011-06-02` + `2026-11-24` 两行，若直接用前一行收盘价当昨收，会产生严重失真的振幅。
- 防护规则：计算每日 `(high - low) / prev_close` 时，检查相邻两根日 K 的自然日间隔 `(bar_date - prev_date).days`：
  - 正常跨周末/长假间隔不会超过 15 天（A 股春节/国庆最长约 10~11 个自然日，美股最长 4 个自然日；放宽至 `30` 个自然日以兼容短期停牌）。
  - 若 `(bar_date - prev_date).days > 30`，视为历史断档/占位行，**重置基准**（更新 `prev_close` 与 `prev_date`，但跳过该日振幅计算），彻底杜绝用多年旧价算当日振幅。

---

## 4. 路由装配设计（`backend/app/api/v1/router.py`）

在 [router.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/v1/router.py#L87-L91) 中：
- `quote_collector = QuoteCollector(create_quote_provider())`
- 从 `quote_collector.provider` 中提取 `LongbridgeQuoteProvider`（兼容 `PriorityFallbackQuoteProvider.primary` 或直接就是 `LongbridgeQuoteProvider`）：
  ```python
  longbridge_provider: LongbridgeQuoteProvider | None = None
  if isinstance(quote_collector.provider, LongbridgeQuoteProvider):
      longbridge_provider = quote_collector.provider
  elif isinstance(quote_collector.provider, PriorityFallbackQuoteProvider) and isinstance(
      quote_collector.provider.primary, LongbridgeQuoteProvider
  ):
      longbridge_provider = quote_collector.provider.primary
  ```
- 将 `longbridge_kline_fetcher=longbridge_provider.get_daily_candlesticks if longbridge_provider is not None else None` 传入 `DailyAmplitudeProvider`。
- 这样：
  - `quote_provider == "longbridge"` → 自动启用长桥日 K 主源 + 腾讯/东财备源；
  - `quote_provider == "eastmoney"` → 不建长桥连接，美股使用修好后缀的腾讯日 K 主源 + 东财备源；
  - `quote_provider == "mock"` → `network_enabled=False`，零网络访问。

---

## 5. 兼容性与回滚

- **向后兼容**：`DailyAmplitudeProvider` 新增参数均有默认值 `None`，不破坏现有调用方与既有单元测试。
- **已有缓存清理**：此前若因美股失败未写入 `security_daily_metrics`，上线后首次打开 Dashboard 会立即触发刷新并落库；若个别美股此前被腾讯无后缀占位行写入了 `sample_days == 1` 的历史记录，刷新新鲜度判定仍会按最新已收盘交易日覆盖更新。
- **回滚方案**：本变更仅涉及 3 个后端代码文件与单测，无数据库变更，可直接 `git revert` 无损回滚。
