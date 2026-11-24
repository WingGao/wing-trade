# 执行计划：场外宽基基金盘中指数估算

## 实施步骤清单

### Step 1：核验东财指数代码映射 & 数据库增量迁移
- [ ] 核验东方财富 `suggest` / `ulist.np` / `trends2` 对常见 A 股宽基指数（沪深300 `000300`、中证500 `000905`、中证1000 `000852`、上证50 `000016`、创业板指 `399006`、科创50 `000688`、中证A500 `000510`）的返回格式与市场码。
- [ ] 更新 `backend/app/models/ledger.py` 中 `Security` 模型，新增 `proxy_symbol`、`proxy_name`、`tracking_ratio` 字段并补充中文注释。
- [ ] 创建 Alembic 迁移脚本，验证在现有 SQLite 结构上平滑升级（严禁删除或清空 `ledger.db`）。

### Step 2：后端行情源与指数查名支持
- [ ] 更新 `backend/app/market_data/security_info.py`：`search()` 正确识别 A 股指数为 `INDEX`（保留 `SH`/`SZ` 市场）；确保 `_intraday_eastmoney()` 正常拉取指数当日分钟分时。
- [ ] 更新 `backend/app/market_data/provider.py`：`QuoteSnapshot` 扩展估算元数据字段；确认 `EastmoneyQuoteProvider` 正常支持指数批量报价。

### Step 3：后端估算派生引擎、采集联动与 API
- [ ] 更新 `backend/app/market_data/service.py`：在 `QuoteService` 实现 `resolve_effective_snapshot()` 与批量读取时的参考标的自动关联估算逻辑（含日期对齐与降级策略，确保不改写 `QuoteCache` 与不误触发 `evaluate_targets`）。
- [ ] 更新 `backend/app/market_data/collector.py`：刷新时自动附带采集已绑定基金对应的 `proxy_symbol` 实时行情。
- [ ] 更新 `backend/app/api/schemas/security.py`、`backend/app/api/schemas/common.py`（及对应 dashboard/quote schema）补充新增字段。
- [ ] 新增 `PATCH /api/v1/securities/{security_id}/proxy` 接口，校验 `OF` 市场、参考标的合法性与 `trackingRatio` 范围，并确保参考标的在 `securities` 表建档以复用共享缓存。
- [ ] 更新 `backend/app/api/v1/market.py`：`/securities/{id}/intraday` 支持 `series=auto|nav`，在 `nav_date < 今日` 时按公式换算返回 `seriesKind="estimated_intraday"`。
- [ ] 更新 `backend/app/services/dashboard.py` 与 `/api/v1/positions` / `/api/v1/quotes` / `/api/v1/securities/{id}/lots` 统一输出估算后的报价字段。

### Step 4：前端类型、API、绑定交互与盘中一致展示
- [ ] 更新 `frontend/src/api/types.ts` 与 `frontend/src/api/client.ts`：补充 `proxySymbol`、`proxyName`、`trackingRatio`、`isEstimated`、`officialNav` 等类型定义及 `updateSecurityProxy`、`intraday(id, series)` 调用。
- [ ] 新增场外基金跟踪标的绑定弹窗组件（或内嵌组件），支持输入代码/名称自动搜索选择、默认 `0.95` 可选微调与一键解绑。
- [ ] 更新 `frontend/src/components/QuoteBadge.vue`：支持 `估` 状态标签与估算明细 tooltip（参考标的、涨跌幅、仓位系数、昨官方净值）。
- [ ] 更新 `frontend/src/pages/SecurityDetailPage.vue` 与 `frontend/src/components/IntradayChart.vue`：已绑定参考标的的场外基金启用盘中 3 秒轮询、展示当日估算分时图并支持切换「近 30 日净值」。
- [ ] 检查 `frontend/src/pages/DashboardPage.vue` 与 `frontend/src/components/LotTable.vue`：确认持仓列表、看板汇总、移动端紧凑视图与止盈预估达标展示与股票完全一致。

### Step 5：自动化测试与质量门禁
- [ ] 后端单元测试：估算公式精确性、日期对齐（`nav_date < proxy_date` vs `nav_date >= proxy_date`）、参考标的缺失/过期降级、指数查名与分时换算、止盈目标不因盘中估算落库固化。
- [ ] 后端集成测试：`PATCH /securities/{id}/proxy` 校验与解绑、Dashboard/Lots/Intraday 端到端闭环。
- [ ] 前端类型检查、构建及测试验证。

---

## 验证命令

```bash
# 后端测试
cd backend && uv run pytest

# 前端类型检查与构建
cd frontend && pnpm typecheck && pnpm build
```

## 风险文件与回滚点

- **高关注文件**：
  - `backend/app/market_data/service.py`（核心缓存读写与止盈触发边界，必须确保官方净值缓存不被估算值覆盖）
  - `backend/app/market_data/collector.py`（批量刷新与熔断逻辑，避免引入循环或重复请求）
  - `backend/app/services/dashboard.py`（全账户估值汇总与 `valuationStatus` 覆盖状态）
  - `frontend/src/pages/SecurityDetailPage.vue`（轮询生命周期与竞态保护）
- **回滚点**：数据库迁移独立成单一 revision，业务代码按「后端模型/行情 -> 后端 API -> 前端展示」分层提交，必要时可独立回滚。

## 启动前检查（`task.py start` 之前）

- [x] `prd.md` 已完成需求收敛并通过验收标准检查
- [x] `design.md` 已明确数据边界、API 契约与回滚策略
- [x] `implement.md` 已列出有序步骤与验证命令
- [ ] 用户审阅并明确确认开始实施
