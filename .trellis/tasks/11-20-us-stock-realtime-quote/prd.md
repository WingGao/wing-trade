# 支持美股和实时行情

## 目标（Goal）

扩展当前个人交易账本，完整支持美股（US）资产的日常管理（账户切换、持仓展示、流水与复盘）以及可靠、低延迟的实时行情能力（覆盖 A 股与美股）。

## 已确认的代码库现状

1. **领域模型与币种隔离（已部分具备）**
   - `backend/app/domain/markets.py` 已定义 `MARKET_CURRENCY`：`SH/SZ/BJ -> CNY`，`US -> USD`。
   - `backend/app/models/ledger.py` 与 `backend/app/api/deps.py` 已支持 `CNY`/`USD` 账户，并强制校验账户币种必须与证券所属市场币种一致（`AccountCurrencyMismatchError`）。
   - `backend/app/market_data/calendar.py` 已定义 `USTradingCalendar`（`America/New_York` 9:30–16:00），`QuoteService` 已按市场区分缓存过期日历。
2. **证券查名与日内曲线（已部分具备，但存在缺陷）**
   - `backend/app/market_data/security_info.py` 的 `search()` 已支持通过东方财富接口搜索美股（市场码 `105/106/107`）。
   - `intraday()` 已尝试拉取美股分时，但市场码映射仅保存在内存 LRU（`_us_markets`）未持久化；解析时间戳时使用了 A 股时区，且未覆盖盘前/盘后时段。
   - 详情页目前采用每 3 秒 HTTP 轮询 `/api/v1/securities/{id}/intraday`（后端 2.5 秒防抖）。
3. **当前核心缺口**
   - **美股批量行情缺失**：`EastmoneyQuoteProvider`（`backend/app/market_data/provider.py:220`）仅支持 `SH/SZ/BJ` 6 位数字代码，直接过滤 `US:*`，导致总览页无法批量刷新美股行情。
   - **总览交易时段写死为 A 股**：`DashboardService.build()`（`backend/app/services/dashboard.py:54`）仅使用 A 股日历判断 `marketSessionOpen` 与 `pollAfterSeconds`（开盘 10s / 休市 60s），导致美股开盘时段（北京时间夜间）被误判为休市。
   - **前端多账户/美股工作流不完整**：总览页（`DashboardPage.vue`）缺少顶部账户切换器，默认只加载 CNY 账户；侧边栏标题仍标注“A 股交易账本”；交易流水/复盘页的账户切换与多币种体验需补齐。
   - **实时行情机制**：目前全站为 HTTP 定时轮询（总览 10s/60s，详情 3s），尚无更低延迟的推送（如 SSE/WebSocket）或专业美股实时数据源接入。

## 需求（Requirements）

- 待结合用户决策补充收敛。

## 验收标准（Acceptance Criteria）

- [ ] 待需求收敛后补充可验证的验收标准。

## 范围外（Out of Scope）

- 待确认。

## 待决问题（Open Questions）

1. **美股行情源与“实时行情”方案选择**（当前阻塞）：采用零配置公开数据源、需 API Key 的专业实时源，还是双模式？
2. **美股交易时段与扩展时段**：是否需要支持美股盘前（Pre-market）/ 盘后（After-hours）行情与显示？
3. **实时更新形态**：首页与详情页期望保持高频短轮询（如 3–5 秒），还是引入服务端推送（SSE / WebSocket）？
4. **美股账户与页面展示范围**：是否需要补齐首页/流水/复盘的账户切换、USD 账户管理、以及美股碎股/手续费等细节适配？
