# 实施计划：美股 20 日振幅接入长桥日 K 主源与腾讯备源修复

## 执行步骤

### Step 1. 扩展 `LongbridgeQuoteProvider` 日 K 线能力
- 文件：`backend/app/market_data/longbridge_adapter.py`
- 任务：
  - 为 `LongbridgeQuoteProvider` 新增 `get_daily_candlesticks(self, symbol: str, *, count: int = 25) -> list[Any]` 方法（附完整中文文档注释）。
  - 复用 `to_longbridge_symbol`、`_get_context()` 与 `_lock`，调用 `ctx.candlesticks(lb_symbol, Period.Day, count, AdjustType.ForwardAdjust, TradeSessions.Intraday)`（兼容 4 参数签名）。
  - 调用失败时清空 `self._ctx` 并向上抛出异常。

### Step 2. 改造 `DailyAmplitudeProvider` 接入长桥主源并修复腾讯美股备源
- 文件：`backend/app/market_data/amplitude.py`
- 任务：
  - `DailyAmplitudeProvider.__init__` 新增可选参数 `longbridge_kline_fetcher`。
  - 新增 `_fetch_longbridge_kline_bars`：使用 `Decimal` 解析 `Candlestick` 的 `timestamp`/`close`/`high`/`low`，按对应市场时区归属交易日，计算 `(high - low) / prev_close * 100%` 并应用相邻日期间隔（`<= 30` 天）校验。
  - 修复 `_fetch_tencent_us_kline_bars`：支持按交易所后缀（`.OQ` / `.N` / `.AM`）构造请求与顺序探测，优先读取缓存市场码映射的后缀，命中后写回 `USMarketResolver`；在逐日计算振幅时加入相邻日期间隔（`<= 30` 天）防占位行污染校验。
  - 调整 `_fetch_bars_for_symbol` 路由：
    - `SH`/`SZ`/`US` 且配置了 `longbridge_kline_fetcher` 时优先走长桥（`longbridge-kline`），失败或空时平滑回退；
    - 美股备源优先走腾讯带后缀日 K（`tencent-us-kline`），失败或空时再回退东方财富美股日 K（`eastmoney-us-kline`）；
    - A 股备源走东方财富日 K（`eastmoney-kline`）。

### Step 3. 路由层注入长桥日 K 拉取器
- 文件：`backend/app/api/v1/router.py`
- 任务：
  - 从 `quote_collector.provider` 中提取 `LongbridgeQuoteProvider`（含 `PriorityFallbackQuoteProvider.primary`），将其 `get_daily_candlesticks` 注入 `daily_amplitude_service` 的 `DailyAmplitudeProvider`。

### Step 4. 单元测试与验证
- 文件：`backend/tests/unit/test_daily_amplitude.py`、`backend/tests/unit/test_longbridge_adapter.py`
- 新增测试场景：
  1. `LongbridgeQuoteProvider.get_daily_candlesticks` 正常映射、参数传递、`BJ`/`OF` 跳过及异常清空上下文；
  2. `DailyAmplitudeProvider` 在配置长桥时优先使用长桥日 K（美股 + A 股），正确排除当日盘中未收盘 K 线并产出 `longbridge-kline`；
  3. 长桥拉取抛错或返回空时平滑回退到腾讯美股（带后缀）/ 东财日 K；
  4. 腾讯美股日 K 按 `.OQ` / `.N` / `.AM` 后缀探测成功并记忆交易所编号，且过滤 `2011-06-02` 类多年断档占位行不产出失真振幅；
  5. `router.py` 装配验证（不触发真实网络）。

### Step 5. 规范同步与质量检查
- 更新 `.trellis/spec/backend/market-data-guidelines.md` 中“个股近 20 个已收盘交易日平均振幅”章节。
- 运行后端完整测试套件与语法检查。

## 验证命令

```bash
cd backend && uv run pytest tests/unit/test_daily_amplitude.py tests/unit/test_longbridge_adapter.py -v
cd backend && uv run pytest
```

## 质量门禁

- [ ] 所有新增与修改方法均带有中文注释
- [ ] 不触碰、不清空本地 `ledger.db`
- [ ] 后端 `pytest` 全部通过，无新增告警或跳过失败
- [ ] 自动化测试零公网依赖
- [ ] `.trellis/spec/backend/market-data-guidelines.md` 已同步更新
