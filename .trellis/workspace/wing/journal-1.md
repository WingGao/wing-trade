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
