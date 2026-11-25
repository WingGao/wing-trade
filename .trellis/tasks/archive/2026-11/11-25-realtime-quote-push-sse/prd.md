# 长桥实时推送行情与前端 SSE 事件流改造 PRD

## 1. 目标与用户价值

- **核心问题**：当前行情链路完全依赖前端定时轮询（首页 `DashboardPage.vue` 每 10 秒/60 秒全量重查 `/api/v1/dashboard`，详情页 `SecurityDetailPage.vue` 每 3 秒串行请求 `intraday` 与 `lots`），且后端 [collector.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/collector.py) 仅在收到请求时同步访问公网并直接写库 `quote_cache`，导致行情延迟高、数据库写入频繁、后台无常驻行情状态、页面切换与多标签开销大。
- **产品目标**：接入**长桥 OpenAPI（Longbridge）**主动推送渠道，在后端构建进程级内存行情中心（`QuoteHub`），并通过 **SSE（Server-Sent Events）** 在前端建连后持续推送实时报价；前端基于首屏精确账务基线与实时报价流在本地派生展示估值并实时更新详情页分时图最新点，彻底摆脱常规状态下的高频定时轮询。
- **用户价值**：
  - 盘中报价秒级/准实时推送，首页与详情页体验流畅；
  - 后端内存直读 + 节流落盘，消除高频 SQLite/MySQL 写入竞争；
  - 账务与行情进一步解耦，行情渠道故障或断线时账务读写仍完全可用。

## 2. 已确认事实与约束

- **上游渠道与凭据**：接入长桥 OpenAPI，采用开发者后台三元组配置（`app_key`、`app_secret`、`access_token`），并支持可选美股夜盘开关（`enable_overnight`）。
- **市场覆盖与兜底策略**：
  - 未配置长桥（默认 `quote.provider=eastmoney`）时保持既有东财/腾讯行为；
  - 启用 `quote.provider=longbridge` 时，`SH` / `SZ` / `US`（含盘前/盘中/盘后，按配置可选夜盘）走长桥主动推送；
  - 长桥不支持的场外开放式基金（`OF`）与北交所（`BJ`），以及长桥暂不可用/未覆盖的标的，自动回退现有东财/腾讯 HTTP 行情源；
  - 绑定了 `proxy_symbol` 的场外基金，其参考标的收到实时推流后，后端内存立即派生估算净值并随 SSE 推送；
  - `quote.provider=mock` 继续保持完全离线、可确定复现，供自动化测试使用。
- **估值职责边界（方案 A）**：首屏、切换账户、记账或重算后仍调用后端 `/api/v1/dashboard` 与 `/api/v1/securities/{id}/lots` 获取精准账务事实（`remainingQty`、`remainingCost`、`averageCost`、`lowestPriceGroup`、`realizedPnl`、现金流、`needsRefresh` 等）；盘中 SSE 仅推送报价快照，前端使用无浮点误差的十进制运算工具派生展示市值、浮盈、收益率与账户汇总，绝不回写服务端账务。
- **推送协议**：前后端采用 **SSE**（`GET /api/v1/quotes/stream`），复用现有 `wing_session` HttpOnly Cookie 鉴权（见 [deps.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/deps.py#L43-L53)）。
- **项目硬约束**：
  - 本地 `ledger.db` 不允许删除或清空数据；
  - 所有文档使用中文，所有新增/修改方法必须有清晰注释；
  - 后端行情与账务计算严格使用 `Decimal`，前端派生估值不得引入二进制浮点精度漂移。

## 3. 功能需求

### R1. 长桥配置与依赖接入
- 扩展 [config.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/config.py) 的 `_YAML_SCHEMA`、`Settings` 与环境变量解析，新增 `quote.provider="longbridge"` 及 `quote.longbridge` 配置节：
  - `app_key` / `LONGBRIDGE_APP_KEY`
  - `app_secret` / `LONGBRIDGE_APP_SECRET`
  - `access_token` / `LONGBRIDGE_ACCESS_TOKEN`
  - `enable_overnight` / `LONGBRIDGE_ENABLE_OVERNIGHT`（默认 `false`）
  - 可选端点覆盖（`http_url`、`quote_ws_url`，留空走 SDK 默认路由）
- 保持 `YAML > env > 默认值` 优先级；当 `quote.provider="longbridge"` 且缺少 `app_key` / `app_secret` / `access_token` 任一项时，在配置加载阶段明确抛 `ValueError`。
- 同步更新 [config.example.yaml](file:///media/wing/Bt/WingProjs/wing-trade/config.example.yaml) 与 [compose.yaml](file:///media/wing/Bt/WingProjs/wing-trade/compose.yaml)，并在 [backend/pyproject.toml](file:///media/wing/Bt/WingProjs/wing-trade/backend/pyproject.toml) 引入 `longbridge` 依赖（同时在代码层延迟导入，确保离线与 mock 测试无外部耦合）。

### R2. 长桥主动推流适配器
- 实现 `LongbridgeQuoteStream`（以及用于单次拉取/基线补齐的 `LongbridgeQuoteProvider`）：
  - 规范代码互转：`SH:600519` ↔ `600519.SH`、`SZ:000001` ↔ `000001.SZ`、`US:AAPL` ↔ `AAPL.US`；
  - 订阅新标的时先调用 `ctx.quote()` 获取 `SecurityQuote` 基线（含 `prev_close`、`last_done` 及美股扩展时段基准），再调用 `ctx.subscribe(..., [SubType.Quote], is_first_push=True)` 接收增量 `PushQuote`；
  - 回调在 SDK 后台线程触发，须通过 `loop.call_soon_threadsafe` 投递至 FastAPI 主事件循环；
  - 使用 `Decimal` 精确合并增量价格、计算 `change_pct` 并映射 `market_session`（`pre`/`regular`/`post`/`closed`）；
  - 单连接遵守 500 标的订阅上限保护，支持断线指数退避重连与健康状态上报。

### R3. 后端内存行情中心（`QuoteHub`）与节流落盘
- 在 [main.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/main.py) 应用生命周期（`lifespan`）中管理 `QuoteHub` 启动与优雅关闭；`mock` 模式或测试隔离场景下不主动连接公网。
- 内存中心维护：
  - 进程级最新原始报价表 `dict[symbol, QuoteSnapshot]`；
  - 动态订阅池 = `全库 OPEN 持仓 ∪ 全库自选股 ∪ 场外基金 proxy_symbol ∪ 在线 SSE 连接附加的临时标的`，支持在买入/清仓/作废/自选变更/修改基金 proxy/前端订阅变更时增量同步；
  - 场外基金代理关系表：当 `proxy_symbol` 收到新推流时，立即在内存调用既有的日期对齐估算规则派生对应 `OF` 基金报价并广播；
  - 后台低频兜底采集：针对长桥不支持的 `OF`（官方净值）与 `BJ`（北交所）或推流异常时的兜底标的，复用现有东财/腾讯 HTTP 源按交易时段节流补齐；
  - 异步节流落盘（Write-behind）：每 30~60 秒及优雅停机时将有变化的原始报价批量写入 `quote_cache` 表，重启冷启动时从 `quote_cache` 预热内存，禁止逐 tick 写库。
- 既有 `GET /api/v1/dashboard`、`GET /api/v1/quotes`、`GET /api/v1/securities/{id}/lots` 在内存存在有效快照时优先直读内存，避免阻塞等待公网 HTTP；`mode=manual` 或 `refresh=true` 仍支持触发一次主动同步。

### R4. 后端 SSE 事件推送端点与反代适配
- 新增 `GET /api/v1/quotes/stream`（鉴权复用 `get_current_user`，支持可选 `symbols` 参数追加临时关注标的，如详情页证券）：
  - 建连成功立即发送 `event: snapshot`，包含当前用户可见持仓/自选及请求追加标的的最新内存快照（含场外基金估算元数据）；
  - 后续收到报价变更时按短窗口（如 300ms）合并去重，发送 `event: quote` 批量增量；
  - 每 20~25 秒发送 `: heartbeat` 注释帧维持长连接；
  - 客户端断开时及时释放队列与临时订阅引用计数，避免内存与订阅泄漏。
- 同步更新 [frontend/nginx.conf](file:///media/wing/Bt/WingProjs/wing-trade/frontend/nginx.conf)（对 `/api/v1/quotes/stream` 禁用 `proxy_buffering`、启用 HTTP/1.1 并设置长读取超时）。

### R5. 前端全局 SSE Store 与首页实时派生估值
- 升级 [quotes.ts](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/stores/quotes.ts) 为统一实时行情 Store：
  - 维护单一 `EventSource`（支持动态追加临时标的如详情页证券）、连接状态（`idle`/`connecting`/`live`/`reconnecting`/`offline`）与断线指数退避重连；
  - 登出或 401 时主动关闭连接并清空状态。
- 新增前端精确十进制估值派生工具（不依赖二进制 `Number` 做核心加减乘除），用于：
  - 基于首屏 `/dashboard` 的 `remainingQty`、`remainingCost`、`lowestPriceGroup` 与 SSE 最新报价，实时计算单持仓总价值、浮盈、收益率、最低价组浮盈及顶部账户汇总；
  - 严格沿用既有 `valuationStatus` 覆盖规则（`partial` / `unavailable` 时账户总浮盈与总市值保持不可用，不伪造局部小计；`stale` 清晰标注参考估值）。
- 更新 [DashboardPage.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/pages/DashboardPage.vue)：
  - 首屏、切换账户、记账/重算后拉取精准 `/dashboard` 基准；
  - SSE 处于活跃态时**停止 10 秒定时轮询**，行情与浮盈由 SSE 实时驱动，涨幅排序持续沿用当前排序方向；
  - 保留「刷新行情」手动按钮及 SSE 长时间不可用时的兜底提示/降级能力；
  - 保持桌面端与移动端高密度视图一致、无布局破坏。

### R6. 详情页实时报价、批次浮盈与分时图联动
- 更新 [SecurityDetailPage.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/pages/SecurityDetailPage.vue)：
  - 首屏与切换证券/账户/记账后拉取一次历史 `intraday` + `lots`；
  - 通过全局 SSE Store 订阅当前证券（已绑定 proxy 的场外基金同时关注其自身估算推送），实时更新顶部报价、批次表逐批浮盈/收益率与 `holdingSummary.unrealizedPnl`（空仓或无有效行情规则保持不变）；
  - 分时图（`seriesKind` 为 `intraday` 或 `estimated_intraday` 时）：收到新报价后按分钟桶更新当日分时最后一个点或追加新分钟点，不破坏悬停交互与 0% 基准线；基金 `seriesKind="nav"` 不追加盘中点；
  - SSE 处于活跃态时**移除 3 秒定时轮询**；SSE 不可用时平滑降级，不阻塞页面与记账入口。

## 4. 验收标准

- **AC1（配置与向后兼容）**：未配置长桥时默认 `eastmoney` 正常运行；`config.example.yaml` 可通过 `Settings.load` 校验；`quote.provider=longbridge` 缺三元组任一项时报错清晰；`YAML > env > 默认值` 对布尔假值（如 `enable_overnight: false`）生效。
- **AC2（长桥推流解析与基线）**：单元测试（注入 mock SDK，不访问公网）验证 `SH`/`SZ`/`US` 代码映射、`quote()` 补齐 `prev_close`、`PushQuote` 增量合并计算精确 `Decimal` `change_pct`、美股盘前/盘中/盘后/夜盘时段映射、非正数价格过滤与线程安全投递。
- **AC3（多市场路由与兜底）**：启用 `longbridge` 时，`SH`/`SZ`/`US` 走长桥订阅，`OF` 与 `BJ` 自动走东财源补齐；长桥参考标的推送更新时，已绑定 `proxy_symbol` 的 `OF` 基金内存即时重算估算净值并广播。
- **AC4（内存优先与节流落盘）**：高频推流期间不逐 tick 写库，定时/停机批量写回 `quote_cache`；重启后能从 `quote_cache` 预热内存；`/dashboard`、`/quotes`、`/lots` 优先读取内存最新报价。
- **AC5（SSE 协议与鉴权）**：未登录请求 `GET /api/v1/quotes/stream` 返回 401；登录后立即收到 `snapshot` 事件，后续内存更新触发节流合并的 `quote` 事件；断连后正确回收订阅与内存队列；用户 A 仅接收自身关注标的及显式请求的公开报价。
- **AC6（首页无轮询实时更新）**：E2E 验证 SSE 建连后首页不再按 10s 轮询 `/dashboard`，推送新价格后单仓市值、现价、涨幅、持仓浮盈、最低价组浮盈、账户总价值与总浮盈实时同步更新且保持精确数值与排序规则；缺行情时不伪造账户合计。
- **AC7（详情页无 3 秒轮询实时更新）**：E2E 验证 SSE 建连后详情页不再每 3 秒轮询 `intraday` / `lots`，推送新价格后顶部报价、批次浮盈、总浮盈与分时图最新分钟点实时更新；切股/离页/登出正确清理订阅，不串数据。
- **AC8（异常降级）**：长桥断线或 SSE 不可用时，前端保留最后有效参考估值并清晰提示，买卖/编辑/重算等账务操作完全不受影响。
- **AC9（质量门禁）**：后端 `ruff`、`mypy`、`pytest` 全量通过；前端 `pnpm run type-check`、`pnpm run build`、Playwright E2E 全量通过；本地 `ledger.db` 未被删除或清空。

## 5. 不在本次范围内（Out of Scope）

- 实时止盈提醒触发推送与弹窗通知（明确延后至后续迭代，保留既有数据库止盈语义不破坏）；
- 港股（`HK`）账户/币种体系新增与港股行情展示；
- 长桥交易下单、持仓同步或 OAuth 2.0 网页授权流；
- 多进程/多节点分布式行情广播（如 Redis Pub/Sub），当前聚焦单实例内存架构；
- 盘口五档/逐笔成交明细（Depth / Trades）展示。
