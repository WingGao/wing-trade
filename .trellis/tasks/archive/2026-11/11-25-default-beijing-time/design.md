# 技术设计：前后端时间默认为北京时间（强时区统一处理）

## 1. 架构与边界

遵循“**底层存储 UTC 绝对时刻 + 接口边界强时区校验与统一北京时间（`+08:00`）输出**”的设计：

```
前端表单/展示 (Asia/Shanghai, +08:00)
   │  统一前端方法 (toAwareBeijingIso / requireTimezoneIso)
   │  所有发往后端的时间参数必须显式携带时区（默认 +08:00）
   ▼
后端 API 输入边界 (AwareDateTime -> require_aware_utc)
   │  有 tzinfo → astimezone(UTC)
   │  无 tzinfo → 直接拒绝（422 ValidationError）
   ▼
后端领域/服务/数据库 (SQLite / MySQL 统一 UTC 绝对时刻)
   │  从 DB 读出 naive datetime → assume_utc(dt)
   ▼
后端 API / SSE 输出边界 (format_beijing_iso)
   │  astimezone(BEIJING_TZ).isoformat()  → "YYYY-MM-DDTHH:MM:SS[.ffffff]+08:00"
   ▼
前端展示与编辑 (timeZone: 'Asia/Shanghai', +08:00)
```

### 市场日历边界
- `AShareTradingCalendar` 与 `OpenFundCalendar`：复用 `BEIJING_TZ`（`Asia/Shanghai`）。
- `USTradingCalendar`：内部开闭市、盘前/盘中/盘后判定继续使用 `America/New_York`（正确处理夏令时），对外输出给前端的 `sourceTime`、`fetchedAt`、`points[].time` 统一经由 `format_beijing_iso` 转为 `+08:00` 北京时间。

## 2. 后端设计

### 2.1 集中式时间模块 `backend/app/domain/time.py`
新建 `backend/app/domain/time.py` 作为后端唯一时间处理模块：

- `BEIJING_TZ: tzinfo`：优先 `ZoneInfo("Asia/Shanghai")`，无系统 tzdata 时回退 `timezone(timedelta(hours=8), "Asia/Shanghai")`。
- `now_utc() -> datetime`：返回当前 UTC aware `datetime`（ORM 默认值、缓存新鲜度、token/session 过期计算）。
- `now_beijing() -> datetime`：返回当前北京时间 aware `datetime`。
- `assume_utc(value: datetime) -> datetime`：用于数据库读出值与内部兜底，naive `datetime` 按 UTC 补齐，aware `datetime` 转为 UTC。
- `require_aware_utc(value: datetime) -> datetime`：**统一强时区入参处理方法**。检查 `value.tzinfo is not None and value.utcoffset() is not None`，若无时区则抛出 `ValueError("datetime must include timezone offset")`；有时区则转换为 UTC aware `datetime`。
- `to_beijing(value: datetime) -> datetime`：先 `assume_utc(value)` 再 `.astimezone(BEIJING_TZ)`。
- `format_beijing_iso(value: datetime | None) -> str | None`：**统一出参序列化方法**。`None` 返回 `None`，否则返回 `to_beijing(value).isoformat()`（始终带 `+08:00`，完整保留微秒）。

### 2.2 统一入参校验（Schema & Service）
- 在 [backend/app/api/schemas/common.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/schemas/common.py) 定义公共类型：
  ```python
  AwareDateTime = Annotated[datetime, AfterValidator(require_aware_utc)]
  ```
- 所有接收时间参数的 Pydantic Schema 字段（`BuyTradeRequest.executed_at`、`SellPreviewRequest.executed_at`、`SellTradeRequest.executed_at`、`TradeUpdateRequest.executed_at` 等）统一声明为 `AwareDateTime`（或 `AwareDateTime | None`）。
  - 传入无时区字符串（如 `"2026-11-25T09:30:00"`）时，Pydantic 自动触发 422 校验错误；
  - 传入带 `+08:00`、`Z` 或其他偏移字符串时，统一转为 UTC aware `datetime` 传入 Service。
- [backend/app/services/ledger.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/services/ledger.py) 的 `buy`、`sell`、`edit_trade` 中，对 `executed_at` 统一通过 `assume_utc`（或校验 aware）规范为 UTC，确保写入数据库和 `_trade_edit_snapshot` 比较时均基于 UTC 绝对时刻。

### 2.3 统一出参序列化（REST & SSE）
所有对外输出时间的出口统一替换为 `format_beijing_iso`：
- [backend/app/api/v1/router.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/v1/router.py)：移除分散的 `_dt` / `_dt_utc`，全部使用 `format_beijing_iso`（覆盖交易、配对、止盈目标、批次、已结清明细、卖出预览、周期、报价、看板 `asOf`）。
- [backend/app/api/v1/auth.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/v1/auth.py)：移除 `_dt_utc`，改用 `format_beijing_iso`（覆盖 `lastLoginAt`、`createdAt`）。
- [backend/app/api/v1/market.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/api/v1/market.py)：`_format_quote_payload` 的 `sourceTime`/`fetchedAt`、分时接口的 `points[].time` 与 `fetchedAt` 全部使用 `format_beijing_iso`。
- [backend/app/market_data/hub.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/hub.py)：`_fmt_dt` 委托给 `format_beijing_iso`（覆盖 SSE 推流的 `sourceTime`、`fetchedAt`、`serverTime`）。

### 2.4 日志与容器环境
- [backend/app/main.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/main.py)：配置全局日志 `Formatter` 的时间转换器为北京时间。
- [Dockerfile](file:///media/wing/Bt/WingProjs/wing-trade/Dockerfile)、[backend/Dockerfile](file:///media/wing/Bt/WingProjs/wing-trade/backend/Dockerfile)、[compose.yaml](file:///media/wing/Bt/WingProjs/wing-trade/compose.yaml)：默认设置 `TZ=Asia/Shanghai`。

## 3. 前端设计

### 3.1 集中式时间模块 `frontend/src/utils/time.ts`（并从 `format.ts` 兼容导出）
新建 `frontend/src/utils/time.ts`，作为前端所有时间处理的统一入口：

- `BEIJING_TIMEZONE = 'Asia/Shanghai'`
- `BEIJING_OFFSET = '+08:00'`
- `hasTimezoneOffset(value: string): boolean`：判断是否包含 `Z` / `z` 或 `[+-]HH:MM` 时区后缀。
- `nowBeijingPickerValue(now?: Date): string`：使用 `Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', ... })` 拼装 `YYYY-MM-DDTHH:mm`，替代 `toISOString().slice(0, 16)`。
- `toAwareBeijingIso(value: string): string`：**统一前端请求参数处理方法**。
  - 若 `value` 已携带显式时区（且可被解析为有效日期），标准化输出带 `+08:00`（或保留原有效时区并校验合法性）；
  - 若 `value` 为日期选择器受控格式 `YYYY-MM-DDTHH:mm` 或 `YYYY-MM-DDTHH:mm:ss`，明确拼装为 `${datePart}T${timePart}+08:00`；
  - 若格式非法则抛出错误，确保发往后端的所有时间参数均合法且带时区。
- `requireTimezoneIso(value: string): string`：用于手输场景（如编辑交易弹窗），强校验必须带显式时区且为有效时间。
- `toBeijingIsoString(value: string): string`：将服务端返回的 ISO 时间（含微秒）精确换算为 `+08:00` 北京时间字符串，完整保留原有小数秒，不因 `Date` 毫秒截断丢失微秒精度。
- `formatDateTime(value: string | null | undefined): string`：锁定 `timeZone: 'Asia/Shanghai'`、`hour12: false`。
- `formatBeijingAxisTime(stamp: number, mode: 'date-short' | 'date-full' | 'time'): string`：锁定 `timeZone: 'Asia/Shanghai'`，供分时图/净值图复用。

并在 [frontend/src/utils/format.ts](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/utils/format.ts) 重新导出 `formatDateTime` 等方法，保持既有引用零破坏。

### 3.2 组件改造
- [BuyTradeDialog.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/components/BuyTradeDialog.vue)：
  - 初始与每次打开弹窗时，将 `form.executedAt` 重置为 `nowBeijingPickerValue()`；
  - 提交时经由 `toAwareBeijingIso(form.executedAt)` 生成带 `+08:00` 的 `executedAt`。
- [SellMatchDrawer.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/components/SellMatchDrawer.vue)：
  - 初始与每次打开抽屉时，将 `form.executedAt` 重置为 `nowBeijingPickerValue()`；
  - `executedAtIso()` 改用 `toAwareBeijingIso(form.executedAt)`。
- [EditTradeDialog.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/components/EditTradeDialog.vue)：
  - 预填通过 `toBeijingIsoString(trade.executedAt)` 展示为 `+08:00` 北京时间（保留微秒）；
  - 校验通过 `requireTimezoneIso(form.executedAt)` 强制要求带时区；未修改时不提交 `executedAt`，不触发多余 `revision` 递增。
- [IntradayChart.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/components/IntradayChart.vue)：
  - `formatAxisTime` 与 `formatTooltipTime` 改用 `formatBeijingAxisTime`。
- [AdminUsersPage.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/pages/AdminUsersPage.vue)：
  - 删除私有 `formatDateTime`，改用统一的 `formatDateTime`。

## 4. 兼容性、风险与回滚

- **既有后端测试适配**：
  - 若既有 API 测试传入无时区 `executedAt`（未带 `Z` 或 `+08:00`），新契约下将返回 422；需检查并更新相应测试夹具为显式带时区字符串，同时新增专门测试断言无时区入参被拒绝。
  - 若既有测试硬编码断言输出以 `+00:00` / `Z` 结尾，需更新为 `+08:00` 并保留绝对时刻验证。
- **数据库零迁移**：不修改表结构，不触碰 `ledger.db`；回滚直接 `git revert` 即可。
