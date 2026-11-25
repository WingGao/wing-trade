# 长桥实时推送行情与前端 SSE 事件流执行计划

## 1. 实施顺序清单

### 阶段一：配置与长桥行情适配（Backend）
- [x] **1.1 依赖与配置模板**
  - 更新 `backend/pyproject.toml`，添加 `longbridge` 运行依赖（支持 Python 3.14 预编译 wheel）。
  - 更新 `backend/app/config.py`：扩展 `SUPPORTED_QUOTE_PROVIDERS`、`quote.longbridge` YAML 白名单 schema、环境变量解析与三元组必填校验。
  - 同步更新 `config.example.yaml` 与 `compose.yaml`。
- [x] **1.2 长桥适配器实现**
  - 新建 `backend/app/market_data/longbridge_adapter.py`：实现规范代码互转、`SecurityQuote` 基线解析、`PushQuote` 增量合并与 `Decimal` 涨跌幅计算、美股盘前/盘中/盘后/夜盘时段映射、`LongbridgeQuoteProvider` 及 `LongbridgeQuoteStream`（线程安全桥接、500 标的上限保护、指数退避重连）。
  - 更新 `backend/app/market_data/factory.py` 与 `backend/app/market_data/__init__.py`：`quote_provider="longbridge"` 时组装「长桥主源 + 东财 A 股(BJ)/美股备源 + 东财场外基金源」组合适配器。
- [x] **1.3 阶段一单元测试**
  - 更新 `backend/tests/unit/test_config.py` 覆盖长桥 YAML/env 优先级、布尔假值、缺必填项报错及 `config.example.yaml` 校验。
  - 新增 `backend/tests/unit/test_longbridge_adapter.py`（完全注入 mock SDK，不连外网）验证代码映射、基线补齐、增量合并、美股扩展时段、非正数过滤、上限裁剪与异常降级。

### 阶段二：内存行情中心（`QuoteHub`）与 SSE 接口（Backend）
- [x] **2.1 实现 `MarketDataHub`**
  - 新建 `backend/app/market_data/hub.py`：实现内存行情存储、冷启动 `quote_cache` 预热、动态订阅池（持仓 ∪ 自选 ∪ proxy ∪ SSE 临时引用计数）、场外基金 proxy 实时内存重算、300ms 合并广播总线、30s 节流异步落盘与后台兜底采集（`OF`/`BJ`/`eastmoney` 模式）。
- [x] **2.2 接入 FastAPI 生命周期与既有读/写链路**
  - 更新 `backend/app/main.py`：使用 `lifespan` 启动与优雅停止 `QuoteHub`（`mock` 模式不启公网任务）。
  - 更新 `backend/app/market_data/service.py`、`backend/app/market_data/collector.py`、`backend/app/services/dashboard.py`：支持优先读取内存最新快照，活跃推流期间 `auto` 模式零阻塞返回。
  - 更新 `backend/app/api/v1/router.py`：在买入、卖出、作废、自选增删、基金 proxy 变更、重算成功后触发 `quote_hub.refresh_scope_from_db()`。
- [x] **2.3 实现 SSE 接口与反代配置**
  - 在 `backend/app/api/v1/router.py` 新增 `GET /api/v1/quotes/stream`：鉴权校验、首帧 `snapshot`、增量合并 `quote`、状态帧 `status`、25s 心跳、断连资源回收。
  - 更新 `frontend/nginx.conf` 补充 `/api/v1/quotes/stream` 的防缓冲与长超时反代配置。
- [x] **2.4 阶段二单元与集成测试**
  - 新增 `backend/tests/unit/test_quote_hub.py`：覆盖内存发布、proxy 基金联动估算、300ms 聚合广播、节流落盘、动态订阅增删与引用计数回收。
  - 新增 `backend/tests/integration/test_quote_stream_api.py`：覆盖未登录 401、建连首帧 `snapshot`、实时 `quote` 事件推送、多用户隔离及内存优先 Dashboard 估值。

### 阶段三：前端精确派生估值、全局 SSE Store 与页面改造（Frontend）
- [x] **3.1 精确十进制估值与分时合并工具**
  - 新建 `frontend/src/utils/valuation.ts`：实现基于 `BigInt` 的精确十进制算术、`projectDashboardWithQuotes`、`projectLotsWithQuote`、`mergeQuoteIntoIntraday`。
- [x] **3.2 全局实时行情 Store 与应用保活**
  - 重构 `frontend/src/stores/quotes.ts`：实现单一 `EventSource` 生命周期、状态机（`idle`/`connecting`/`live`/`reconnecting`/`offline`）、指数退避重连、动态临时标的注册 `watchSymbol`。
  - 更新 `frontend/src/api/types.ts`（新增流状态类型）与 `frontend/src/App.vue`（登录后自动连接、登出/401 断开并清理状态）。
- [x] **3.3 首页与详情页实时化**
  - 更新 `frontend/src/pages/DashboardPage.vue`：消费 `projectDashboardWithQuotes` 实时投影，SSE `live` 时停止 10s 定时轮询，展示轻量推送状态，保留手动刷新与 `offline` 降级轮询。
  - 更新 `frontend/src/pages/SecurityDetailPage.vue`：注册当前证券临时订阅，消费 `projectLotsWithQuote` 与 `mergeQuoteIntoIntraday` 实时更新报价、批次浮盈、总浮盈与分时图最新点，SSE `live` 时停止 3s 定时轮询。
- [x] **3.4 前端 E2E 测试与回归**
  - 新增 `frontend/e2e/realtime_quote_sse.spec.ts`：mock SSE 事件流，验证首页建连无轮询实时重算总市值/浮盈、详情页无 3s 轮询实时更新批次浮盈与分时图新点。

### 阶段四：全量验收与规范同步
- [x] 运行全量后端与前端检查、单测、集成测试、构建与 E2E。
- [x] 按 Trellis Phase 3.3 更新 `.trellis/spec/backend/market-data-guidelines.md`、`.trellis/spec/frontend/state-management.md` 与 `.trellis/spec/frontend/portfolio-detail-guidelines.md` 中的新契约。

---

## 2. 验证命令

```bash
# 后端依赖同步（安装 longbridge 时执行一次）
uv sync --cache-dir .uv-cache --project backend

# 后端静态检查与测试
uv run --cache-dir .uv-cache --offline --no-sync --project backend ruff check app tests
uv run --cache-dir .uv-cache --offline --no-sync --project backend mypy app
uv run --cache-dir .uv-cache --offline --no-sync --project backend pytest -v

# 前端类型检查、构建与 E2E
pnpm --dir frontend run type-check
pnpm --dir frontend run build
pnpm --dir frontend run test:e2e
```

---

## 3. 高风险文件与回滚检查点

| 关键文件 | 风险说明 | 回滚/隔离点 |
| :--- | :--- | :--- |
| `backend/app/config.py` | 涉及全局 YAML 白名单与环境变量回退，误改会导致启动失败 | 保留 `eastmoney` 默认值，新字段全部收敛在 `quote.longbridge` 下 |
| `backend/app/main.py` | 切换 `on_event("startup")` 到 `lifespan` 需保持原有表结构迁移、默认管理员初始化及全部测试夹具行为不变 | 先验证 `test_database_startup.py` 与全量集成测试 |
| `backend/app/services/dashboard.py` | 涉及账户估值覆盖状态核心契约 | 仅在 `_load_snapshots` 接入内存直读，不改动任何账务核算公式 |
| `frontend/src/pages/DashboardPage.vue` & `SecurityDetailPage.vue` | 涉及多项现有 E2E 契约（排序、代次防护、待刷新标记、移动高密度布局） | 严格保留代次防护与 `offline` 降级路径；所有派生计算限定在只读 `computed` 投影层 |
| `backend/ledger.db` | 用户真实本地账本数据库 | **严禁删除或清空**；所有测试使用独立内存/临时 SQLite |

---

## 4. 启动实施前检查（`task.py start` 之前）

- [x] `prd.md` 已完成收敛检查，无未决阻塞问题
- [x] `design.md` 已完整定义架构、数据契约、降级与回滚策略
- [x] `implement.md` 已列出分阶段清单与验证命令
- [x] `research/longbridge-sdk-notes.md` 已落盘
- [x] `implement.jsonl` 与 `check.jsonl` 已配置真实 spec 与 research 条目
- [ ] 等待用户审阅计划并明确同意后，再执行 `task.py start 11-25-realtime-quote-push-sse` 进入实现
