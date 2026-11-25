# 前后端时间默认为北京时间

## 目标与用户价值

将前后端默认时间口径统一为**北京时间（`Asia/Shanghai` / `UTC+8`）**，并强制要求前后端所有时间参数均携带显式时区且经由统一方法处理，消除以下痛点：
- 买入/卖出表单默认成交时间使用 UTC 导致比北京时间慢 8 小时；
- 交易编辑弹窗将时间标注为 `Z`（UTC），不便于国内用户直接核对与修改；
- 后端 REST/SSE 接口时间输出不统一（混用无时区字符串与 `+00:00`）；
- 前后端时间参数缺少统一的时区校验与规范化方法，存在无时区时间隐式漂移风险；
- 前端时间展示依赖浏览器本地时区，未显式锁定北京时间；
- 容器与日志默认采用 UTC 时区。

## 核心决策与约束

1. **存储与兼容策略（方案 A）**：
   - 数据库（SQLite / MySQL）底层**继续统一按 UTC 绝对时刻存取**，100% 兼容本地 `ledger.db` 中已有历史 UTC 记录，零数据迁移风险。
   - 本地 `ledger.db` 不得删除或清空数据。
2. **强时区参数契约与统一处理方法**：
   - **前后端所有时间参数（请求入参 & 响应出参）必须携带显式时区**（如 `+08:00` 或 `Z`），禁止无时区时间在接口边界流通。
   - 后端通过统一方法（`require_aware_utc` / `AwareBeijingDateTime` 公共类型与 `format_beijing_iso`）集中处理所有入参校验与出参序列化：入参缺失时区时直接返回校验错误（422），合法带时区入参转为 UTC 落库；所有出参统一输出带 `+08:00` 的北京时间 ISO-8601 字符串。
   - 前端通过统一方法（集中于 `frontend/src/utils/time.ts` 或 `format.ts`）处理所有时间生成、转换、校验与展示：所有发往后端的时间参数必须经统一方法产出并携带显式时区（默认 `+08:00`）；所有展示均锁定 `Asia/Shanghai`。
3. **市场交易时段边界**：
   - 美股交易时段（盘前/盘中/盘后/休市）判定仍基于美东时间（`America/New_York`），不受默认展示时区影响。

## 需求清单

### R1. 后端统一时区与强时区参数处理模块
- 新增集中式后端时区模块 `backend/app/domain/time.py`，提供统一方法：
  - `BEIJING_TZ`：`Asia/Shanghai`（无系统 `tzdata` 时回退固定 `UTC+8`）；
  - `now_utc()` / `now_beijing()`；
  - `assume_utc(dt)`：将数据库读出的无时区时间按 UTC 还原为 aware `datetime`；
  - `require_aware_utc(dt)`：统一入参处理方法——**强制校验 `dt` 必须携带时区信息（`tzinfo is not None and utcoffset() is not None`）**，缺失时抛出校验错误，通过后转换为 UTC aware `datetime`；
  - `to_beijing(dt)` / `format_beijing_iso(dt)`：统一出参序列化方法——按 UTC 还原后转为北京时间并输出带 `+08:00` 的 ISO-8601 字符串。
- 在 Pydantic Schema 层（如 `backend/app/api/schemas/common.py` 定义 `AwareDateTime` 注解类型或统一 validator）应用于所有接收时间的接口入参（`BuyTradeRequest`、`SellPreviewRequest`/`SellTradeRequest`、`TradeUpdateRequest` 等），拒绝无时区时间入参。
- 在 [backend/app/services/ledger.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/services/ledger.py) 的 `buy`、`sell`、`edit_trade` 中统一使用 `require_aware_utc` / `assume_utc` 规范化时间。

### R2. 后端 REST 与 SSE 接口统一输出带 `+08:00` 的北京时间
- 统一替换 [router.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/v1/router.py#L157-L171) 的 `_dt()` / `_dt_utc()`、[auth.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/v1/auth.py#L24-L29) 的 `_dt_utc()`、[hub.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/hub.py#L45-L50) 的 `_fmt_dt()`、[market.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/v1/market.py#L49-L50) 及 `dashboard` 相关 `asOf` / `fetchedAt` / `sourceTime` / 分时点 `time` 等所有对外时间字段，统一调用 `format_beijing_iso` 输出带 `+08:00` 的北京时间。
- 保留微秒精度（编辑交易对比时仍支持精确比对，不因格式化截断导致误判变更）。

### R3. 后端日志与容器部署默认北京时间
- [main.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/main.py#L31-L39) 日志格式器使用北京时间输出时间戳。
- [Dockerfile](file:///media/wing/Bt/WingProjs/wing-trade/Dockerfile)、[backend/Dockerfile](file:///media/wing/Bt/WingProjs/wing-trade/backend/Dockerfile)、[compose.yaml](file:///media/wing/Bt/WingProjs/wing-trade/compose.yaml) 默认配置 `TZ=Asia/Shanghai`。

### R4. 前端统一北京时间与强时区参数处理模块
- 在前端新增/集中提供统一时间处理工具（`frontend/src/utils/time.ts`，并在 [format.ts](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/utils/format.ts) 中导出兼容 `formatDateTime`）：
  - `BEIJING_TIMEZONE = 'Asia/Shanghai'`、`BEIJING_OFFSET = '+08:00'`
  - `hasTimezoneOffset(value)`：校验 ISO 字符串是否显式包含时区（`Z` 或 `[+-]HH:MM`）
  - `nowBeijingPickerValue()`：返回当前北京时间的 `YYYY-MM-DDTHH:mm`（用于日期时间选择器默认值）
  - `toAwareBeijingIso(value)`：统一请求参数处理方法——将选择器值（`YYYY-MM-DDTHH:mm`）或带时区 ISO 字符串统一转为带 `+08:00` 的 ISO 字符串；若传入无时区且非选择器受控格式的非法字符串则报错/拒绝，确保所有发往后端的时间参数必然带时区
  - `formatBeijingDateTime(value)` / `formatBeijingAxisTime(stamp, mode)`：显式指定 `timeZone: 'Asia/Shanghai'` 与 24 小时制
  - `toBeijingIsoString(value)`：将服务端返回时间无损转换为带 `+08:00` 的字符串（保留原小数秒/微秒），供编辑弹窗预填展示
- 表单录入与编辑组件：
  - [BuyTradeDialog.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/components/BuyTradeDialog.vue#L13)：打开时默认填充当前北京时间，提交时通过统一方法生成带 `+08:00` 的 `executedAt`。
  - [SellMatchDrawer.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/components/SellMatchDrawer.vue#L26)：打开时默认填充当前北京时间，预览与提交均通过统一方法生成带 `+08:00` 的 `executedAt`。
  - [EditTradeDialog.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/components/EditTradeDialog.vue#L19-L53)：预填通过统一方法展示为 `+08:00` 北京时间（保留微秒）；提交时强校验必须带时区（如 `+08:00` 或 `Z`），未修改时不触发 `revision` 递增。
- 展示组件：
  - [IntradayChart.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/components/IntradayChart.vue#L38-L53)：横轴与悬浮提示统一使用 `formatBeijingAxisTime`。
  - [AdminUsersPage.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/pages/AdminUsersPage.vue#L136-L140)：复用统一 `formatDateTime`。

## 范围外

- 不新增用户级自定义时区切换功能。
- 不修改现有数据库表结构，不对 `ledger.db` 执行历史数据时间迁移。
- 不改变美股交易日历内部的美东时段判定规则。

## 验收标准

- [ ] 后端所有 REST 接口（交易、批次、周期、看板、持仓详情、分时、认证/用户管理）与 SSE 推流事件中的时间字段均经由统一方法输出带 `+08:00` 的北京时间 ISO-8601 字符串。
- [ ] 后端所有接收时间的 API 接口均经由统一方法校验：传入无时区 `executedAt`（如 `2026-11-25T09:30:00`）时明确返回 422 校验错误；传入带 `+08:00` 或 `Z` 等显式时区的时间时正确按绝对时刻入库，并回显对应的 `+08:00` 北京时间。
- [ ] 现有 `ledger.db` 中已有记录（按 UTC 落库）读取后自动正确换算为北京时间（`+08:00`），历史记录时间不发生 8 小时偏移。
- [ ] 前端所有发往后端的时间参数（买入、卖出预览、卖出提交、编辑交易）均经由前端统一时间方法处理并强制携带显式时区（默认 `+08:00`）。
- [ ] 前端打开买入弹窗和卖出抽屉时，默认成交时间等于当前北京时间（`Asia/Shanghai`）；编辑交易弹窗打开时，成交时间以 `+08:00` 北京时间展示且未改动直接保存时不递增 `revision`。
- [ ] 前端在非东八区（例如 `TZ=UTC`）环境下验证时，`formatDateTime`、分时图时间轴与用户管理时间仍稳定输出北京时间。
- [ ] 后端日志时间戳与 Docker/Compose 容器环境默认使用北京时间（`Asia/Shanghai`）。
- [ ] 后端 pytest、编译检查与前端 type-check / 构建 / 单元测试全部通过。
