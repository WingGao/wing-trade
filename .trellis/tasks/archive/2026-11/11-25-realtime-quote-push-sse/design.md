# 长桥实时推送行情与前端 SSE 事件流技术设计

## 1. 总体架构与分层边界

整体链路拆分为四层，各层通过明确的数据契约解耦：

```text
[长桥 WebSocket 推流 (SH/SZ/US)]     [东财/腾讯 HTTP 兜底 (OF/BJ/回退)]
                 \                                 /
                  v                               v
        ┌───────────────────────────────────────────────────┐
        │          MarketDataHub（进程级内存行情中心）        │
        │  - 内存最新报价表 (symbol -> QuoteSnapshot)        │
        │  - 动/静态订阅池 (持仓 ∪ 自选 ∪ proxy ∪ SSE 临时)  │
        │  - 场外基金 Proxy 内存实时估算派生                  │
        │  - 节流落盘器 (Write-behind -> quote_cache)        │
        │  - 发布/订阅总线 (按用户/标的过滤 + 300ms 聚合)    │
        └──────────────────────┬────────────────────────────┘
                               │
              ┌────────────────┴────────────────┐
              v                                 v
   [REST API 优先读内存]             [GET /api/v1/quotes/stream]
   (/dashboard, /lots, /quotes)      (SSE: snapshot / quote / heartbeat)
                                                │
                                                v
                                   [前端全局 useQuotesStore]
                                   (单一 EventSource + 自动重连)
                                      /                 \
                                     v                   v
                          [DashboardPage.vue]   [SecurityDetailPage.vue]
                          精确十进制派生估值       实时批次浮盈 + 分时图追加点
```

### 层间职责原则
1. **行情仍是派生数据**：任何推流异常、token 过期、网络中断、SSE 断连都不得阻塞买卖、作废、重算或账务查询。
2. **单一事实源**：账务事实（持股数、剩余成本、平均成本、最低价组基准、已实现收益、待刷新标记 `needsRefresh`）始终来自后端 REST；盘中实时价格流只覆盖行情与派生浮盈展示。
3. **单实例内存前提**：当前项目以单进程 Uvicorn 容器部署（[compose.yaml](file:///media/wing/Bt/WingProjs/wing-trade/compose.yaml)），进程级 `MarketDataHub` 即可满足全用户共享行情，避免引入外部消息中间件。

---

## 2. 后端详细设计

### 2.1 配置扩展（`backend/app/config.py`）

- `SUPPORTED_QUOTE_PROVIDERS` 扩展为 `frozenset({"eastmoney", "longbridge", "mock"})`。
- `_YAML_SCHEMA["quote"]` 新增嵌套节：
  ```yaml
  quote:
    provider: eastmoney # eastmoney | longbridge | mock
    timeout_seconds: 5
    longbridge:
      app_key: ""
      app_secret: ""
      access_token: ""
      enable_overnight: false
      http_url: ""
      quote_ws_url: ""
  ```
- 环境变量映射（遵循 `YAML > env > 默认值`，空字符串对凭据视作未提供，布尔假值不回退 env）：
  - `LONGBRIDGE_APP_KEY`
  - `LONGBRIDGE_APP_SECRET`
  - `LONGBRIDGE_ACCESS_TOKEN`
  - `LONGBRIDGE_ENABLE_OVERNIGHT`
  - `LONGBRIDGE_HTTP_URL`
  - `LONGBRIDGE_QUOTE_WS_URL`
- 校验规则：
  - 当 `quote_provider == "longbridge"` 时，`longbridge_app_key`、`longbridge_app_secret`、`longbridge_access_token` 三项均不能为空，否则抛出明确 `ValueError`；
  - `quote_provider` 为 `eastmoney` 或 `mock` 时，长桥凭据允许为空，确保现有环境与测试零侵入。

### 2.2 长桥行情适配层（`backend/app/market_data/longbridge_adapter.py`）

为保持模块边界清晰与离线可测性，新增独立文件 `backend/app/market_data/longbridge_adapter.py`：

1. **代码映射工具**
   - `to_longbridge_symbol(canonical: str) -> str | None`：
     - `SH:600519` -> `600519.SH`
     - `SZ:000001` -> `000001.SZ`
     - `US:AAPL` -> `AAPL.US`
     - `BJ:*` / `OF:*` -> 返回 `None`（不走长桥）。
   - `from_longbridge_symbol(lb_symbol: str) -> str | None`：反向映射为规范大写 `MARKET:CODE`。

2. **基线与增量合并逻辑（纯函数可单测）**
   - 维护每只订阅标的的 `LongbridgeBaseline`：`prev_close: Decimal | None`、`regular_close: Decimal | None`、`last_snapshot: QuoteSnapshot | None`。
   - **订阅前基线拉取**：调用 `ctx.quote(lb_symbols)` 获取 `SecurityQuote`：
     - 读取 `prev_close`、`last_done`、`timestamp`；
     - 若为美股且存在有效 `pre_market_quote` / `post_market_quote` / `overnight_quote`，结合当前交易阶段选择活跃价并保存 `regular_close = last_done` 作为盘后涨幅基准。
   - **增量推送解析**：收到 `PushQuote` 时：
     - 忽略 `last_done <= 0` 或缺失的报文；
     - 映射 `trade_session` -> `MarketSessionPhase`（`Normal`/`Intraday` -> `regular`，`Pre` -> `pre`，`Post`/`Overnight` -> `post`，其余结合市场日历判定）；
     - 美股盘后时段基准价优先取 `regular_close`，否则取 `prev_close`；A 股与美股常规/盘前取 `prev_close`；
     - 用 `round_price((last_price - ref_close) / ref_close * Decimal("100"))` 精确计算 `change_pct`；
     - `source_time` 取报文时间戳转 UTC，`fetched_at` 取本地 UTC 接收时间，保持既有时间契约。

3. **长连接与线程安全桥接（`LongbridgeQuoteStream`）**
   - 依赖注入工厂（`context_factory`）：生产环境延迟 `from longbridge.openapi import Config, QuoteContext, SubType` 创建真实上下文；测试中可注入 fake context。
   - `start(loop, on_snapshot)`：在后台线程/任务建立 `QuoteContext` 并注册 `set_on_quote`，回调内通过 `loop.call_soon_threadsafe(on_snapshot, snapshot)` 推入 `QuoteHub`。
   - `sync_subscriptions(desired_symbols: set[str])`：
     - 过滤出长桥支持的标的并裁剪至最多 500 只（超限时优先保留持仓 > proxy > 自选 > 临时查看，并打警告日志）；
     - 仅对差集 `added = desired - current` 调 `ctx.quote(added)` + `ctx.subscribe(added, [SubType.Quote], is_first_push=True)`；
     - 对 `removed = current - desired` 调 `ctx.unsubscribe(removed, [SubType.Quote])`。
   - 断线与建连失败保护：捕获异常，更新健康状态（`connected` / `reconnecting` / `error`），采用指数退避（5s -> 10s -> 20s -> 最大 60s）重建上下文并自动重订阅全部活跃标的。
   - 同时提供同步 `LongbridgeQuoteProvider`（实现 `QuoteProvider` 协议，单次调 `ctx.quote()`），供 `mode=manual` 或 `QuoteCollector` 复用。

### 2.3 进程级内存行情中心（`backend/app/market_data/hub.py`）

新增 `MarketDataHub`，作为应用唯一行情内存中枢：

1. **数据结构**
   - `_quotes: dict[str, QuoteSnapshot]`：全市场最新原始报价（`OF` 基金这里仅存官方净值，保持缓存纯净，绝不把估算值覆盖原始净值）。
   - `_dirty_symbols: set[str]`：自上次落盘后有更新的原始报价代码集合。
   - `_base_symbols: set[str]`：数据库持久范围（`OPEN 持仓 ∪ 自选股 ∪ 场外基金 proxy_symbol`）。
   - `_ephemeral_refs: dict[str, int]`：在线 SSE 客户端附加临时标的（如详情页查看未持仓证券）的引用计数。
   - `_fund_proxies: dict[str, list[ProxyBinding]]`：`proxy_symbol -> [绑定的 OF 基金元数据]`，用于参考标的推流到达时 `O(k)` 即时重算估算净值。
   - `_subscribers: set[StreamSubscriber]`：当前活跃 SSE 订阅者集合，每个订阅者持有：
     - `user_id: str`
     - `allowed_symbols: set[str]`（用户自身持仓+自选+临时查看标的）
     - `queue: asyncio.Queue[dict[str, QuoteSnapshot]]`（有界队列，防慢客户端拖垮内存）

2. **核心运行协程（由 FastAPI `lifespan` 启停）**
   - **启动预热**：从数据库加载 `quote_cache` 填充 `_quotes`、加载初始 `_base_symbols` 与 `_fund_proxies`。
   - **上游驱动**：
     - 当 `quote_provider == "longbridge"`：启动 `LongbridgeQuoteStream` 订阅 `SH/SZ/US`，同时启动后台低频轮询协程仅负责 `OF` 官方净值（每 10~30 分钟或缺失时）与 `BJ`/推流未覆盖标的（交易时段每 10~15 秒）。
     - 当 `quote_provider == "eastmoney"`：后台采集协程按市场交易日历（A 股/美股交易时段每 5~10 秒，休市降频）拉取并写入内存，**即使未配置长桥，前端也能享受 SSE 推送与内存直读**！
     - 当 `quote_provider == "mock"`：不启动公网后台任务，支持测试代码直接调用 `hub.publish(snapshots)` 注入行情并触发 SSE 广播。
   - **场外基金估算联动**：当 `publish()` 收到某 `symbol` 新报价且 `_fund_proxies` 中存在绑定该 `symbol` 的 `OF` 基金时，结合内存中的基金官方净值调用 `QuoteService.resolve_effective_snapshot` 立即派生有效估算快照，一并向关注该基金的 SSE 订阅者广播。
   - **300ms 节流合并广播**：`publish()` 先更新 `_quotes` 与待广播缓冲区 `_pending_broadcast`，启动/复用 300ms 定时器批量分发给匹配的订阅者队列，兼顾低延迟与前端渲染流畅度。
   - **Write-behind 异步落盘**：后台每 30 秒检查 `_dirty_symbols`，在独立 DB Session 中批量 `upsert` 到 `quote_cache`（注意：本次不触发实时止盈提醒持久化逻辑变化，落盘仅同步报价快照）；应用关闭 `stop()` 时再执行最后一次 flush。

3. **与既有 REST 与写接口的衔接**
   - **写操作触发订阅同步**：在 [router.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/v1/router.py) 的买入、卖出、作废、添加/删除自选、修改基金 proxy、重算完成后，非阻塞触发 `quote_hub.refresh_scope_from_db()`（同步更新 `_base_symbols`、`_fund_proxies` 与各在线用户的 `allowed_symbols`）。
   - **读接口优先读内存**：
     - `QuoteService` / `QuoteCollector` / `DashboardService` 支持可选绑定 `QuoteHub`：
       - `read_cached()` 优先合并内存快照与 DB 缓存（以更新的 `fetched_at` 为准，内存实时态天然最新且零 IO）；
       - 当 `QuoteHub` 处于活跃推流状态时，`DashboardService._load_snapshots(mode="auto")` 若内存中全部支持标的均已有未过期快照，则直接返回内存快照，无需再次同步请求公网；
       - `mode="manual"` 或 `/quotes?refresh=true` 仍显式执行一次即时同步，刷新内存并落盘。

### 2.4 SSE 推送端点（`backend/app/api/v1/market.py` 或 `router.py`）

- 路由：`GET /api/v1/quotes/stream`
- 查询参数：
  - `symbols: str | None`（可选，逗号分隔，最多 50 个，如 `SH:600519`，用于详情页临时订阅）
  - `accountId: str | None`（可选，按账户缩小推送集合；省略时默认推送当前用户全部账户的持仓+自选+临时标的）
- 鉴权：复用 `Depends(get_current_user)`，未登录返回 401（走统一领域错误处理）。
- 响应头：
  - `Content-Type: text/event-stream; charset=utf-8`
  - `Cache-Control: no-cache, no-transform`
  - `Connection: keep-alive`
  - `X-Accel-Buffering: no`（显式告知 Nginx 禁止缓冲）
- 事件协议（统一复用现有 `quote_snapshot_data` camelCase DTO 格式，确保零格式漂移）：
  - 首帧 `event: snapshot`：
    ```json
    {
      "serverTime": "2026-11-25T03:30:00+00:00",
      "provider": "longbridge",
      "streamState": "live",
      "quotes": [ /* Quote[] */ ]
    }
    ```
  - 增量帧 `event: quote`：
    ```json
    {
      "serverTime": "2026-11-25T03:30:01+00:00",
      "quotes": [ /* 仅本批次变动的 Quote[] */ ]
    }
    ```
  - 状态帧 `event: status`（上游推流断连/恢复时可选通知前端展示连接状态）：
    ```json
    {
      "provider": "longbridge",
      "streamState": "reconnecting",
      "message": "行情流重连中"
    }
    ```
  - 心跳帧：每 25 秒发送 `: heartbeat\n\n`。

---

## 3. 前端详细设计

### 3.1 精确十进制派生估值工具（`frontend/src/utils/valuation.ts`）

严格遵循「禁止用 `Number` 浮点运算制造金额漂移」的要求，新增纯 TypeScript `BigInt` 定点十进制运算工具（零第三方大依赖）：
- 内部将任意十进制字符串解析为 `(digits: bigint, scale: number)`，支持精确加、减、乘、除与 `ROUND_HALF_UP` 舍入（金额 2 位 / 内部 4 位、价格 6 位、收益率百分数 6 位，与后端 `app/domain/precision.py` 保持一致）。
- 导出单一投影函数：
  - `projectDashboardWithQuotes(base: DashboardRead, quotesBySymbol: ReadonlyMap<string, Quote>): DashboardRead`
    - 仅合并比当前快照更新（或同源刷新）的有效报价；
    - 逐仓位重算 `unrealizedPnl = remainingQty * lastPrice - remainingCost`、`unrealizedReturnPct = unrealizedPnl / remainingCost * 100`（分母 `<= 0` 为 `null`）；
    - 若存在 `lowestPriceGroup`，同步重算其 `unrealizedPnl` 与 `unrealizedReturnPct`；
    - 聚合账户级 `valuationStatus`（严格复刻后端规则：`empty` / `unavailable` / `partial` / `stale` / `fresh`）、`unrealizedPnl`、`marketValue = remainingCost + unrealizedPnl`、`unrealizedReturnPct`；`partial` 或 `unavailable` 时总市值与总浮盈必为 `null`。
  - `projectLotsWithQuote(lots: Lot[], summary: LotsSummary | null, quote: Quote | null): { lots: Lot[]; summary: LotsSummary | null }`
    - 仅对 `status === 'OPEN'` 且 `remainingQty > 0` 的批次实时派生 `unrealizedPnl` 与 `unrealizedReturnPct`；已结清批次保持原样（显示破折号）；
    - 同步更新 `summary.unrealizedPnl`（无剩余持仓或无有效行情时保持 `null`），不修改 `remainingQty`、`remainingCost`、`realizedPnl`。
  - `mergeQuoteIntoIntraday(base: IntradayRead, quote: Quote): IntradayRead`
    - 仅当 `base.seriesKind !== 'nav'` 且 `quote.lastPrice` 为有效正数时生效；
    - 取 `quote.sourceTime ?? quote.fetchedAt`，按分钟截断（`YYYY-MM-DDTHH:MM`）：
      - 若与末点同分钟，替换末点价格；
      - 若晚于末点分钟，追加新点；
      - 若早于末点（迟到旧包），忽略不倒退曲线；
    - 同步更新 `base.quote` 与 `base.isStale`。

### 3.2 全局实时行情 Store（`frontend/src/stores/quotes.ts`）

- 维护：
  - `quotesBySymbol: Ref<Record<string, Quote>>`（按规范大写 `MARKET:CODE` 索引）
  - `connectionState: Ref<'idle' | 'connecting' | 'live' | 'reconnecting' | 'offline'>`
  - `upstreamState: Ref<string>`（来自 `snapshot`/`status` 事件）
  - `extraSymbols: Ref<Set<string>>`（页面注册的临时关注标的，如详情页当前个股）
- 连接管理：
  - `connect()` / `disconnect()`：应用登录后在 `App.vue` 级统一保活单条 `EventSource('/api/v1/quotes/stream?...')`，退出或 401 时立即 `disconnect()` 并清空缓存；
  - `watchSymbol(symbol: string): () => void`：页面进入时登记临时标的，返回清理函数；仅当新标的尚未在服务端订阅集中时平滑重连带新 `symbols` 参数；
  - 断线重连：监听 `onerror`，关闭旧实例，按指数退避（1s -> 2s -> 5s -> 10s -> 最大 30s）重连；连续失败达到阈值时切为 `offline`，供页面启用低频兜底策略；
  - 标签页可见性：页面隐藏时不主动断开短时切换，若长时间隐藏或断开后恢复可见立即触发重连/校对。

### 3.3 页面集成与轮询收敛

1. **[DashboardPage.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/pages/DashboardPage.vue)**
   - 保留首屏 `cache` -> `auto` 加载流程与交易/重算后的全量重载；
   - 新增计算属性 `liveDashboard = computed(() => ledger.dashboard ? projectDashboardWithQuotes(ledger.dashboard, quotesStore.quotesMap) : null)`，所有表格行、排序、顶部汇总卡片均消费 `liveDashboard`；
   - 在顶部工具栏展示轻量的实时流状态指示（如「实时推送中」/「重连中」），不影响现有标签与移动端布局；
   - **定时轮询收敛**：当 `quotesStore.connectionState === 'live'` 时不启动 `pollTimer`；仅当 SSE 未就绪/降级为 `offline` 时才按服务端 `pollAfterSeconds` 兜底轮询。

2. **[SecurityDetailPage.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/pages/SecurityDetailPage.vue)**
   - 路由进入/切换证券后执行一次 `intraday` + `lots` 加载，并通过 `quotesStore.watchSymbol(canonicalSymbol)` 订阅该标的；
   - 使用 `computed` 将实时报价投影到 `displayLots`、`displayHoldingSummary`、`displayIntraday` 与 `currentQuote`；
   - **定时轮询收敛**：当 `quotesStore.connectionState === 'live'` 时关闭 3 秒 `pollTimer`；仅当 SSE 处于 `offline` 降级状态时保留受控降级轮询（既保证线上零轮询，又在极端反代不支持 SSE 时平滑退化）。

3. **反代与开发环境配置**
   - [frontend/nginx.conf](file:///media/wing/Bt/WingProjs/wing-trade/frontend/nginx.conf)：新增 `location = /api/v1/quotes/stream` 块，设置 `proxy_http_version 1.1`、`proxy_set_header Connection ""`、`proxy_buffering off`、`proxy_cache off`、`proxy_read_timeout 3600s`。
   - [frontend/vite.config.ts](file:///media/wing/Bt/WingProjs/wing-trade/frontend/vite.config.ts)：确认 Vite 代理透传 SSE 流正常（必要时补充 `changeOrigin: true`）。

---

## 4. 兼容性、风险与回滚考量

| 风险点 | 应对措施 |
| :--- | :--- |
| 长桥 `access_token` 过期或网络不可达 | 捕获初始化/重连异常并打印清晰 `WARNING`/`ERROR` 日志，Hub 自动对未获更新标的保留最后缓存并标记过期，或降级到东财/腾讯 HTTP 兜底；不影响账务接口 |
| `longbridge` SDK 未安装或测试环境无外网 | 采用延迟导入 + 依赖注入工厂；`mock` 与 `eastmoney` 模式下完全不导入 `longbridge` |
| SQLite 高频写锁（`database is locked`） | 推流只写内存，落盘改为 30s 定时批量写入 + 停机 flush；严禁在回调线程写库 |
| 既有 Playwright E2E 拦截 `**/api/v1/**` 返回 JSON 导致 `EventSource` 反复重连 | `useQuotesStore` 对非 SSE 响应/断连采用指数退避并在失败后平滑进入 `offline` 降级态，不抛未捕获异常、不覆盖已有视图；同时更新并新增专门覆盖 SSE 推送的 E2E 用例 |
| 单连接超过长桥 500 只订阅上限 | `LongbridgeQuoteStream` 按优先级（持仓 > proxy > 自选 > 临时）裁剪并记录告警，剩余标的走 HTTP 兜底 |
| 需快速回滚到旧轮询模式 | 配置切回 `quote.provider: eastmoney` 即可完全不启用长桥；前端 SSE 断连自动降级到既有 REST 刷新，具备天然开关隔离 |
