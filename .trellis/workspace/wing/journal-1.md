# Journal - wing (Part 1)

> AI development session journal
> Started: 2026-11-05

---



## Session 1: M0 ledger domain and database implementation
<!-- trellis-session: v=2 fp=61e0de61a3686051 -->

**Date**: 2026-11-05
**Task**: M0 ledger domain and database implementation
**Branch**: `master`

### Summary

Implemented the SQLite/Alembic ledger schema, Decimal-only accounting and matching services, cycle and target calculations, transactional ledger operations with idempotency/voiding/audit events, tests, and backend code-spec updates. Compile/AST/domain smoke checks passed; full pytest, ruff, mypy, and Alembic checks were unavailable because dependencies were not installed and offline installation was unavailable.

### Git Commits

| Hash | Message |
|------|---------|
| `a05ab32` | feat: implement M0 ledger domain and database |
| `36a0b32` | docs: codify backend ledger contracts |

### Status

[OK] **Completed**


## Session 2: M0 ledger REST API implementation
<!-- trellis-session: v=2 fp=9f0c953a789f85f5 -->

**Date**: 2026-11-05
**Task**: M0 ledger REST API implementation
**Branch**: `master`

### Summary

Implemented FastAPI /api/v1 ledger routes, Pydantic contracts, standard errors, SQLite write serialization, idempotent buy/sell flows, positions/lots/cycles/targets/analytics queries, health/readiness endpoints, request logging, and API integration coverage. Compileall and git diff checks passed; pytest, ruff, mypy, and runtime import checks were unavailable because dependencies are not installed.

### Git Commits

| Hash | Message |
|------|---------|
| `3633a69` | feat: add M0 ledger REST API |

### Status

[OK] **Completed**


## Session 3: M1 market quote cache implementation
<!-- trellis-session: v=2 fp=278d0096247795b9 -->

**Date**: 2026-11-05
**Task**: M1 market quote cache implementation
**Branch**: `master`

### Summary

Implemented pluggable quote providers with AKShare and mock/fallback adapters, A-share session calendar, shared QuoteCache service, stale detection, single-flight refresh, refresh throttling, circuit breaker backoff, target trigger gating, /api/v1/quotes, and market quote integration tests. Compileall and git diff checks passed; pytest, ruff, and mypy were unavailable because dependencies are not installed.

### Git Commits

| Hash | Message |
|------|---------|
| `c89659a` | feat: add M1 market quote cache |

### Status

[OK] **Completed**


## Session 4: M1 frontend MVP workbench and deployment
<!-- trellis-session: v=2 fp=564514e8d576c8c0 -->

**Date**: 2026-11-05
**Task**: M1 frontend MVP workbench and deployment
**Branch**: `master`

### Summary

Implemented Vue 3 + TypeScript + Vite + Element Plus + Pinia portfolio workbench with dashboard, security detail, lot/settled views, buy/sell preview dialogs, profit targets, trades and analytics pages, typed API client, quote stale indicators, Compose/Docker/nginx deployment, README, and Playwright E2E coverage. Type-check, production build, Playwright 2-test suite, and docker compose config passed.

### Git Commits

| Hash | Message |
|------|---------|
| `a613853` | feat: add frontend MVP workbench |
| `35eb25f` | chore: ignore Playwright artifacts |

### Status

[OK] **Completed**


## Session 5: Complete project development guidelines
<!-- trellis-session: v=2 fp=cdea6826eb56c650 -->

**Date**: 2026-11-06
**Task**: Complete project development guidelines
**Branch**: `master`

### Summary

Filled backend and frontend Trellis specifications with code-backed directory, logging, component, hook, state, and type-safety conventions; updated indexes, checked all bootstrap requirements, and verified no placeholders remain. Git diff check and spec scan passed.

### Git Commits

| Hash | Message |
|------|---------|
| `489058e` | docs: complete project development guidelines |

### Status

[OK] **Completed**


## Session 6: Final A-share MVP integration validation
<!-- trellis-session: v=2 fp=c9e6fe8ff843411f -->

**Date**: 2026-11-06
**Task**: Final A-share MVP integration validation
**Branch**: `master`

### Summary

Completed parent integration review for the A-share portfolio MVP. Backend pytest passed 10 tests after fixing slotted Settings defaults, SQLite naive/aware chronology comparison, and quote provider snapshot iteration. Frontend type-check/build and Playwright passed earlier; docker compose config passed, while full image startup remained unavailable because image pulls did not finish. Archived the completed parent task.

### Git Commits

| Hash | Message |
|------|---------|
| `7bbdcd5` | fix: stabilize full-stack integration validation |

### Status

[OK] **Completed**


## Session 7: Expose quick trade entry in dashboard
<!-- trellis-session: v=2 fp=c395b9345d613252 -->

**Date**: 2026-11-06
**Task**: Expose quick trade entry in dashboard
**Branch**: `master`

### Summary

Recorded the quick trade UX and development runtime fixes in Trellis. The dashboard now searches securities and opens the existing buy dialog or sell preview drawer; the uv backend command uses a project-local cache and python -m uvicorn; the market calendar falls back to fixed UTC+8 when Windows tzdata is unavailable.

### Main Changes

- Added dashboard quick buy/sell entry with security search and selected-lot loading.
- Added Playwright coverage for visible buy and sell actions.
- Documented uv startup and timezone fallback conventions.

### Git Commits

(No commits - planning session)

### Testing

- [OK] pnpm run type-check passed.
- [OK] pnpm run build passed.
- [OK] pnpm run test:e2e passed: 3 tests.

### Status

[OK] **Completed**


## Session 8: US equity support implementation
<!-- trellis-session: v=2 fp=6a356912c92a25e9 -->

**Date**: 2026-11-06
**Task**: US equity support implementation
**Branch**: `master`

### Summary

Implemented isolated USD accounts and US equity/ETF support. Added US market validation and currency isolation, account list/create APIs, US timezone/calendar selection, currency-aware API payloads, frontend account selection and USD buy/sell labels, and regression coverage. Backend pytest passed 11 tests; frontend type-check/build passed; Playwright passed 3 tests. Live US provider remains replaceable/deferred, and fixed-offset New York fallback requires tzdata for DST correctness.

### Main Changes

- Added US:SYMBOL and USD account contracts without FX conversion.
- Added cross-currency rejection and currency-aware frontend quick trade flow.
- Added US calendar selection and integration coverage.

### Git Commits

| Hash | Message |
|------|---------|
| `b06f983` | feat: add isolated US equity support |

### Testing

- [OK] python -m pytest -q: 11 passed.
- [OK] pnpm --dir frontend run type-check/build: passed.
- [OK] pnpm --dir frontend run test:e2e: 3 passed.

### Status

[OK] **Completed**


## Session 9: Dashboard-A股实时行情与浮盈
<!-- trellis-session: v=2 fp=7c5718500ef01758 -->

**Date**: 2026-11-16
**Task**: Dashboard-A股实时行情与浮盈
**Branch**: `11-16-dashboard-realtime-pnl`

### Summary

实现A股实时行情、统一浮盈估值、首页轮询与降级；后端33个测试、前端构建和6个E2E通过。

### Git Commits

| Hash | Message |
|------|---------|
| `be58bf6` | feat:dashboard-realtime-ashare-valuation |

### Status

[OK] **Completed**


## Session 10: 卖出配对最近买入优先
<!-- trellis-session: v=2 fp=a6f1a3e15951d950 -->

**Date**: 2026-11-17
**Task**: 卖出配对最近买入优先
**Branch**: `11-16-dashboard-realtime-pnl`

### Summary

实现LIFO卖出配对和真实买入明细展示及竞态回归测试

### Git Commits

| Hash | Message |
|------|---------|
| `cc568da` | feat:sell-match-latest-buy |

### Status

[OK] **Completed**


## Session 11: 完善持仓看板、批次明细与分级止盈
<!-- trellis-session: v=2 fp=31be7723c1e09b5b -->

**Date**: 2026-11-17
**Task**: 完善持仓看板、批次明细与分级止盈
**Branch**: `master`

### Summary

完成持仓市值、外部查名和日内图、等量配对与批次重建、三级止盈、结清买卖明细，用户评审后提交并归档。

### Main Changes

- 统一持仓估值与三位股价、整数股数，增加真实日内图和外部名称查找。
- 实现等量卖出、事务重建及三级止盈只读继承，显示结清批次全部有效买卖和本批分摊。
- 同步前后端规范与任务验收；归档原目录未跟踪导致自动提交失败，核对后单独补齐归档提交。

### Git Commits

| Hash | Message |
|------|---------|
| `aa10418` | feat: 完善持仓看板、批次明细与分级止盈 |

### Testing

- [OK] 后端72项测试通过，mypy检查53个文件通过；迁移测试仅使用隔离临时库。
- [OK] 前端类型检查、生产构建和18项端到端回归通过，已核对完整明细截图。
- [OK] 本次新增文件和修改行Ruff无新增问题；全库保留164项历史问题；git diff --check通过。

### Status

[OK] **Completed**

### Next Steps

- 尚未推送或部署。部署前核对目标数据库及Alembic基线，迁移应先于新版后端启动；不自动接管用户账本。


## Session 12: 个股详情持仓汇总
<!-- trellis-session: v=2 fp=942091ff00630d21 -->

**Date**: 2026-11-18
**Task**: 个股详情持仓汇总
**Branch**: `feat/trade-edit-refresh`

### Summary

新增个股详情持仓汇总摘要，后端统一计算总持股数、持仓成本、已实现收益与总浮盈，并补充过期行情、空仓和范围切换的集成与 E2E 测试。

### Git Commits

| Hash | Message |
|------|---------|
| `4e9da9f` | feat: 增加个股详情持仓汇总 |

### Status

[OK] **Completed**


## Session 13: 配置 MySQL 后端支持
<!-- trellis-session: v=2 fp=2b4bd7923c78cb1b -->

**Date**: 2026-11-18
**Task**: 配置 MySQL 后端支持
**Branch**: `master`

### Summary

完成 .env 驱动的 MySQL 配置、PyMySQL 连接池、Compose profile、启动迁移和数据库规范；115 个后端测试通过，真实 MySQL 容器因镜像下载受阻未完成。

### Git Commits

| Hash | Message |
|------|---------|
| `7d8a66d` | feat: 支持 MySQL 后端配置与启动迁移 |

### Status

[OK] **Completed**


## Session 14: 后端日志等级支持环境变量配置
<!-- trellis-session: v=2 fp=1496762a1ed9a448 -->

**Date**: 2026-11-20
**Task**: 后端日志等级支持环境变量配置
**Branch**: `master`

### Summary

新增 LOG_LEVEL 统一控制应用与 Uvicorn 日志等级，补充配置校验、Compose 透传、测试和日志规范。

### Git Commits

| Hash | Message |
|------|---------|
| `b5f8bf6` | feat: 支持环境变量配置后端日志等级 |

### Status

[OK] **Completed**


## Session 15: 用户体系、邮箱验证码登录与管理员邀请机制
<!-- trellis-session: v=2 fp=055c3332a303fc13 -->

**Date**: 2026-11-20
**Task**: 用户体系、邮箱验证码登录与管理员邀请机制
**Branch**: `master`

### Summary

实现无密码邮箱验证码登录、30天服务端HttpOnly会话、管理员定向邀请与用户管理、全链路多用户数据隔离（含ProfitSetting）、SQLite/MySQL存量数据无损归属默认管理员、SMTP 465自动SSL推断及前端登录/用户管理界面

### Git Commits

| Hash | Message |
|------|---------|
| `544a088` | feat: 新增用户体系、邮箱验证码登录与管理员邀请机制 |

### Status

[OK] **Completed**


## Session 16: 前端移动端高信息密度视图适配
<!-- trellis-session: v=2 fp=6e539ea19dd211f5 -->

**Date**: 2026-11-20
**Task**: 前端移动端高信息密度视图适配
**Branch**: `master`

### Summary

完成前端除管理员页外的全业务页面与弹窗移动端高信息密度适配（768px 分端视口、零横向溢出、总览活动仓位全展开与排序、详情页双端活动仓位切换与可折叠操作栏/分时图），通过 type-check、build 与全量 37 条 Playwright E2E，并补充前端规范文档。

### Git Commits

| Hash | Message |
|------|---------|
| `0380c9e` | feat: 新增前端移动端高信息密度视图适配与详情页活动仓位切换 |
| `0826c2c` | docs(spec): 补充移动端高信息密度视图契约规范 |

### Status

[OK] **Completed**


## Session 17: 支持优先读取结构化 YAML 配置并回退环境变量
<!-- trellis-session: v=2 fp=57930d2a2de72a79 -->

**Date**: 2026-11-23
**Task**: 支持优先读取结构化 YAML 配置并回退环境变量
**Branch**: `master`

### Summary

实现后端结构化 YAML 优先读取与字段级环境变量回退，补齐 config.example.yaml、.gitignore、compose.yaml、README、141 项测试与后端配置及测试契约规范

### Git Commits

| Hash | Message |
|------|---------|
| `99b8ab6` | feat: 支持优先读取结构化 YAML 配置并回退环境变量 |
| `38367dd` | docs(spec): 补充结构化 YAML 配置契约与后端测试运行约定 |

### Status

[OK] **Completed**


## Session 18: 支持场外开放式基金简单买卖与已公布净值
<!-- trellis-session: v=2 fp=8d8e217564f580ad -->

**Date**: 2026-11-23
**Task**: 支持场外开放式基金简单买卖与已公布净值
**Branch**: `master`

### Summary

新增场外开放式基金（OF/FUND）支持，接入东方财富已公布单位净值行情源与近期净值曲线，适配前端小数份额、四位净值展示并补充个股详情批次成本总价与现价，补齐单测、集成测试与 spec 规范。

### Git Commits

| Hash | Message |
|------|---------|
| `8b6c647` | feat(backend): 支持场外开放式基金建档、已公布单位净值行情与近期净值曲线 |
| `c50d5aa` | feat(frontend): 适配场外基金展示与近期净值图，并补充批次成本总价与现价 |
| `b133823` | docs(spec): 补充场外开放式基金已公布净值与近期曲线契约 |

### Status

[OK] **Completed**


## Session 19: 支持美股公开实时行情、盘前盘后与多账户管理
<!-- trellis-session: v=2 fp=cfd27818fb35070d -->

**Date**: 2026-11-23
**Task**: 支持美股公开实时行情、盘前盘后与多账户管理
**Branch**: `master`

### Summary

实现美股公开批量实时行情（腾讯主源+东财回退，覆盖VOO等ETF）、盘前/盘中/盘后扩展日历与分时回退、Dashboard按账户币种切换日历；前端新增全局账户切换与美元账户创建，补齐总览/流水/复盘多币种及美股碎股与买卖费用录入，后端158项测试与前端构建全绿。

### Git Commits

| Hash | Message |
|------|---------|
| `5ab466c` | feat(backend): 支持美股公开批量实时行情、盘前盘后扩展时段与多账户日历 |
| `2a2bbb4` | feat(frontend): 新增全局账户切换与美元账户管理，适配美股盘前盘后、碎股与费用录入 |
| `618bf7f` | docs(spec): 补充美股公开行情、盘前盘后与扩展日历契约及任务文档 |

### Status

[OK] **Completed**


## Session 20: 场外宽基基金盘中指数估算
<!-- trellis-session: v=2 fp=e9d5c470c2df962f -->

**Date**: 2026-11-24
**Task**: 场外宽基基金盘中指数估算
**Branch**: `master`

### Summary

支持纯A股宽基场外基金绑定跟踪指数/ETF参考标的，盘中按官方净值与参考标的涨跌幅实时派生估算净值、盈亏、止盈提示及估算分时曲线，晚间官方净值公布后自动切回。

### Git Commits

| Hash | Message |
|------|---------|
| `8680ae2` | feat(backend): 支持纯A股宽基场外基金绑定跟踪指数与盘中估算净值/分时 |
| `d78da26` | feat(frontend): 新增场外基金跟踪标的绑定弹窗及盘中估算净值/分时展示 |
| `663cbc7` | docs(spec): 补充场外宽基基金盘中指数估算契约及任务规划文档 |

### Status

[OK] **Completed**


## Session 21: 长桥实时推送行情与前端 SSE 事件流
<!-- trellis-session: v=2 fp=b09c3fcb004bde21 -->

**Date**: 2026-11-25
**Task**: 长桥实时推送行情与前端 SSE 事件流
**Branch**: `master`

### Summary

接入长桥 OpenAPI 实时推流与东财/腾讯备源、进程级内存行情中心 MarketDataHub、/api/v1/quotes/stream SSE 推送端点及前端 BigInt 精确十进制派生估值与零轮询改造，全量后端 172 项测试与前端构建通过。

### Main Changes

- 新增 backend/app/market_data/longbridge_adapter.py 与 quote.longbridge 配置及依赖
- 新增 backend/app/market_data/hub.py 与 GET /api/v1/quotes/stream SSE 推送端点
- 新增 frontend/src/utils/valuation.ts，重构 useQuotesStore 及首页/详情页实时估值
- 同步更新后端与前端 Trellis 规范文档

### Git Commits

| Hash | Message |
|------|---------|
| `486563e` | feat(market-data): 接入长桥 OpenAPI 配置与实时推流适配器 |
| `e450bfd` | feat(market-data): 实现进程级内存行情中心与 SSE 推送端点 |
| `2e42b8a` | feat(frontend): 接入全局 SSE 实时行情与精确十进制派生估值并更新规范 |

### Testing

- [OK] 后端 ruff/mypy/pytest 172 passed，前端 type-check 与 build 通过

### Status

[OK] **Completed**

### Next Steps

- 在具备 Chromium 的环境中执行 pnpm --dir frontend exec playwright install chromium && pnpm --dir frontend run test:e2e


## Session 22: 前后端时间默认为北京时间
<!-- trellis-session: v=2 fp=97f3b5c8728fcb6a -->

**Date**: 2026-11-25
**Task**: 前后端时间默认为北京时间
**Branch**: `master`

### Summary

统一前后端时间口径默认为北京时间（Asia/Shanghai / +08:00），底层保持 UTC 存储兼容 ledger.db；后端新增集中式时间模块与 AwareDateTime 强时区校验，统一 REST/SSE/日志/容器为北京时间；前端新增统一时间模块，修复买卖默认时间慢 8 小时问题并锁定所有展示为北京时间。

### Git Commits

| Hash | Message |
|------|---------|
| `3d5b383` | feat(backend): 统一后端时间参数强时区校验与北京时间输出 |
| `44df945` | feat(frontend): 统一前端默认北京时间与强时区时间参数处理 |

### Status

[OK] **Completed**


## Session 23: 活动仓位与详情页增加上次卖出价并对齐双端展示
<!-- trellis-session: v=2 fp=bfc6d01fc2496d42 -->

**Date**: 2026-11-25
**Task**: 活动仓位与详情页增加上次卖出价并对齐双端展示
**Branch**: `master`

### Summary

后端 dashboard 与 lots 接口新增跨周期最近一笔有效卖出价及相对涨跌幅；前端抽取双端共享 ActivePositionList 与 SecurityDetailPanel，实现设计图三列布局、4项筛选、搜索、紧凑切换、5维正反排序及 PC 端首页右栏个股详情联动，详情页汇总新增上次卖出价并同步更新 Trellis 规范。

### Git Commits

| Hash | Message |
|------|---------|
| `37585c4` | feat(backend): 活动仓位与详情接口增加上次卖出价及相对涨跌幅 |
| `9b0ce83` | feat(frontend): 统一双端活动仓位展示、增加上次卖出价并联动 PC 右栏详情 |

### Status

[OK] **Completed**


## Session 24: 实现三层止盈实时检测与企业微信 Webhook 推送
<!-- trellis-session: v=2 fp=fd44700ec191d1e6 -->

**Date**: 2026-11-25
**Task**: 实现三层止盈实时检测与企业微信 Webhook 推送
**Branch**: `master`

### Summary

新增 notification_channels 与 profit_alert_deliveries 表及 0006 无损迁移，实现可扩展推送通道抽象、企业微信 Webhook 校验/脱敏/Markdown 聚合渲染、按用户隔离配置与一键测试接口、接入 MarketDataHub 的三层止盈自然日去重评估引擎，以及总览桌面端/移动端推送设置弹窗。

### Git Commits

| Hash | Message |
|------|---------|
| `df94672` | feat(backend): 新增三层止盈实时检测引擎与企业微信 Webhook 推送通道 |
| `2006fc8` | feat(frontend): 总览新增企微止盈推送设置弹窗与测试入口 |

### Status

[OK] **Completed**


## Session 25: 个股新增近20个交易日平均振幅并在现价低批上卖列展示
<!-- trellis-session: v=2 fp=537d6d1311c39520 -->

**Date**: 2026-11-25
**Task**: 个股新增近20个交易日平均振幅并在现价低批上卖列展示
**Branch**: `master`

### Summary

新增 security_daily_metrics 缓存表与日 K 线近 20 个已收盘交易日平均振幅服务，扩展总览接口字段，并在活动仓位现价低批上卖列下方新增 20日振 展示行（不改表头与排序）

### Git Commits

| Hash | Message |
|------|---------|
| `0b44d9a` | feat(backend): 新增个股近20个已收盘交易日平均振幅计算、缓存与总览接口字段 |
| `1ce7eba` | feat(frontend): 活动仓位现价低批上卖列新增近20日平均振幅展示行 |

### Status

[OK] **Completed**


## Session 26: 美股20日振幅接入长桥日K、修复腾讯备源与长桥时间戳时区
<!-- trellis-session: v=2 fp=4d0840f76f8c2f13 -->

**Date**: 2026-11-25
**Task**: 美股20日振幅接入长桥日K、修复腾讯备源与长桥时间戳时区
**Branch**: `master`

### Summary

为20日平均振幅接入长桥日K主源并复用QuoteContext，修复腾讯美股日K缺少交易所后缀与历史占位行污染，同时修复长桥无时区本地datetime误作UTC导致美股盘中被判定为盘后的问题。

### Git Commits

| Hash | Message |
|------|---------|
| `a6a4f54` | fix(backend): 美股20日振幅接入长桥日K主源、修复腾讯后缀与长桥本地时间戳时区偏移 |

### Status

[OK] **Completed**


## Session 27: 持仓批次表格列合并与快捷卖出
<!-- trellis-session: v=2 fp=bf119290f0bb82a1 -->

**Date**: 2026-11-26
**Task**: 持仓批次表格列合并与快捷卖出
**Branch**: `master`

### Summary

合并持仓批次桌面端列为一列多行展示（股数/单位成本、成本/现值、浮盈/止盈目标及纵向操作列），并新增批次快捷卖出按钮自动预填剩余股数/份额到卖出弹窗；同步更新前端契约文档。

### Git Commits

| Hash | Message |
|------|---------|
| `fec2835` | feat(frontend): 合并持仓批次表格列并新增批次快捷卖出预填 |

### Status

[OK] **Completed**


## Session 28: 清仓关注与目标买回价提醒
<!-- trellis-session: v=2 fp=5e94c468b270cac9 -->

**Date**: 2026-11-26
**Task**: 清仓关注与目标买回价提醒
**Branch**: `master`

### Summary

支持关注已有/清仓证券、前端开关混入首页列表及目标买回价企微到价提醒，新增 0008 无损迁移与独立 /api/v1/watchlist 聚合接口

### Git Commits

| Hash | Message |
|------|---------|
| `c280b2d` | feat(backend): 新增关注目标买回价、分离查询接口与企微到价提醒 |
| `b6a46ba` | feat(frontend): 支持首页开关混入关注标的及目标买回价交互 |

### Status

[OK] **Completed**


## Session 29: 个股详情页策略表、Markdown/CSV导入及长桥分时接入
<!-- trellis-session: v=2 fp=f9fc05e787dff927 -->

**Date**: 2026-11-26
**Task**: 个股详情页策略表、Markdown/CSV导入及长桥分时接入
**Branch**: `master`

### Summary

在个股详情页新增用户+标的维度共享的买卖策略表（含备注与状态管理）、Markdown/CSV导入解析预览与固定AI提示词复制；同时为日内股价图优先接入长桥OpenAPI分时并补充腾讯A股分时回退。

### Git Commits

| Hash | Message |
|------|---------|
| `f6cae78` | feat(backend): 新增个股买卖策略表持久化接口并支持长桥与腾讯分时回退 |
| `97ebd2c` | feat(frontend): 个股详情页新增买卖策略表、Markdown/CSV导入解析与固定AI提示词复制 |

### Status

[OK] **Completed**


## Session 30: 个股详情页回本卖出测算
<!-- trellis-session: v=2 fp=7ea78465ae8a5420 -->

**Date**: 2026-11-26
**Task**: 个股详情页回本卖出测算
**Branch**: `master`

### Summary

在个股详情页（PC与移动端）新增回本卖出测算按钮与弹窗，实现盈利批+亏损批在±1%盈亏率容差内最大化可卖数量算法及单元测试

### Git Commits

| Hash | Message |
|------|---------|
| `05d7a97` | feat(frontend): 个股详情页新增回本卖出测算弹窗与盈利亏损批配对算法 |

### Status

[OK] **Completed**


## Session 31: 策略网格价格百分比与开关及站点改名
<!-- trellis-session: v=2 fp=46fe03df1b18b19e -->

**Date**: 2026-11-27
**Task**: 策略网格价格百分比与开关及站点改名
**Branch**: `master`

### Summary

买卖策略网格新增买入价较现价百分比、卖出价较买入价百分比及持久化显示开关（桌面/移动端双端适配与单元测试）；全站标题、品牌名与邮件模板统一更名为交易账本；同步前端代码规范。

### Git Commits

| Hash | Message |
|------|---------|
| `554087d` | feat(frontend): 策略网格新增买入/卖出价百分比与显示开关并统一站点名为交易账本 |

### Status

[OK] **Completed**


## Session 32: 回本卖出弹窗新增匹配策略切换
<!-- trellis-session: v=2 fp=454dbf2230abbecb -->

**Date**: 2026-11-27
**Task**: 回本卖出弹窗新增匹配策略切换
**Branch**: `master`

### Summary

在回本卖出测算算法与弹窗中新增「优先消高价（深亏优先）」与「优先多卖（浅亏优先）」策略切换，补充单元测试并同步前端代码规范契约。

### Git Commits

| Hash | Message |
|------|---------|
| `a135c28` | feat(frontend): 回本卖出弹窗新增优先消高价与优先多卖策略切换 |

### Status

[OK] **Completed**


## Session 33: 卖出配对工作台支持手动关联买入批次
<!-- trellis-session: v=2 fp=3dc732ac0cc80aed -->

**Date**: 2026-11-27
**Task**: 卖出配对工作台支持手动关联买入批次
**Branch**: `master`

### Summary

前端卖出配对工作台新增自动/手动模式切换、精确4位定点数量校验、自动预览一键转手动及持仓批次行卖出直达预选，并补充单元测试与前端契约文档。

### Git Commits

| Hash | Message |
|------|---------|
| `ea17ffc` | feat(frontend): 卖出配对工作台支持手动关联买入批次与批次行直达预选 |

### Status

[OK] **Completed**
