# 技术设计：个股近 20 个交易日平均振幅

## 1. 架构与模块边界

本需求严格遵循现有分层边界，不修改账务核心逻辑（`lots` / `trades` / `accounting`），仅在行情数据层、总览读取模型和活动仓位展示组件中增量扩展：

```
上游日 K 线（东财 A/美股主源 + 腾讯美股备源）
        │
        ▼
backend/app/market_data/amplitude.py
  ├── DailyAmplitudeProvider（同步 HTTP 拉取、Decimal 解析、排除当日未收盘、20 日均值计算）
  └── DailyAmplitudeService（DB 缓存读取/写入、按市场日历判断新鲜度、进程内单飞与失败冷却）
        │
        ▼
SQLite 表：security_daily_metrics（Alembic 0007 增量建表）
        │
        ▼
backend/app/services/dashboard.py（DashboardService.build / _position_data）
        │
        ▼
GET /api/v1/dashboard（DashboardPositionRead）
        │
        ▼
frontend/src/api/types.ts → frontend/src/components/ActivePositionList.vue
```

## 2. 数据库结构与迁移

- **新增 ORM 模型**：在 `backend/app/models/ledger.py` 中新增 `SecurityDailyMetric`（表名 `security_daily_metrics`）：
  - `security_id: Mapped[str]` — 主键，外键 `securities.id` (`ON DELETE CASCADE`)
  - `avg_amplitude_20d: Mapped[Decimal | None]` — `ExactDecimal(6)`，百分数（如 `3.450000`）
  - `amplitude_sample_days: Mapped[int | None]` — `Integer`，实际参与计算的已收盘交易日数
  - `amplitude_as_of_date: Mapped[str | None]` — `String(10)`，最近一个已收盘交易日（`YYYY-MM-DD`）
  - `fetched_at: Mapped[datetime | None]` — `DateTime(timezone=True)`，最近一次成功写入时间（UTC）
  - `provider: Mapped[str | None]` — `String(64)`，数据来源（如 `eastmoney-kline` / `tencent-us-kline`）
- **Alembic 迁移**：新增 `backend/alembic/versions/0007_security_daily_metrics.py`（`down_revision = "0006_take_profit_notifications"`），仅执行 `op.create_table("security_daily_metrics", ...)`，严格无损、不触碰本地 `ledger.db` 既有表与数据。

## 3. 日 K 线拉取与计算逻辑（`backend/app/market_data/amplitude.py`）

### 3.1 数据结构
```python
@dataclass(frozen=True, slots=True)
class DailyAmplitudeSnapshot:
    symbol: str
    avg_amplitude_20d: Decimal
    sample_days: int
    as_of_date: str          # YYYY-MM-DD
    fetched_at: datetime     # UTC
    provider: str
```

### 3.2 上游接口与解析
- **A 股（`SH`/`SZ`/`BJ`）**：
  - 接口：`https://push2his.eastmoney.com/api/qt/stock/kline/get`
  - 参数：`secid={1 if SH else 0}.{code}`, `klt=101`, `fqt=0`, `lmt=25`, `end=20500101`, `fields1=f1,f2,f3,f4,f5,f6`, `fields2=f51,f52,f53,f54,f55,f56,f57,f58`
  - 每条 K 线格式：`日期,开,收,高,低,量,额,振幅(%)`
  - 振幅提取：优先使用 `f58`（东财标准 `(高-低)/昨收×100`）；若缺失则使用 `(high - low) / prev_close * 100`（`prev_close` 取前一根有效日 K 收盘价），全程使用 `Decimal`。
- **美股（`US`）**：
  - 主源：东方财富美股日 K（同 `kline/get`，通过 `shared_us_market_resolver()` 解析 `105/106/107`，未命中时依次探测）。
  - 备源：腾讯美股日 K（`https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get?param=us{code},day,,,25,qfq`），解析 `[date, open, close, high, low, ...]` 并用前一日收盘价精确计算 `(high - low) / prev_close * 100`。
- **场外基金（`OF`）**：`supports_symbol()` 直接返回 `False`，不发请求。

### 3.3 排除当日未收盘与 20 日平均
- 计算指定市场当前本地时间 `local_now`：
  - A 股（`Asia/Shanghai`）：若 `local_now` 为工作日且 `local_now.time() < time(15, 0)`，则当日未收盘，过滤掉所有 `bar_date >= local_now.date()` 的记录；否则允许包含 `local_now.date()`。
  - 美股（`America/New_York`）：若 `local_now` 为工作日且 `local_now.time() < time(16, 0)`，则当日未收盘，过滤掉所有 `bar_date >= local_now.date()` 的记录；否则允许包含 `local_now.date()`。
- 从过滤后的有效已收盘日 K 线中按日期升序取最后 `min(len(bars), 20)` 条，计算算术均值并 `round_price`（6 位小数），记录 `sample_days` 与最后一条的 `as_of_date`。

### 3.4 缓存新鲜度与并发控制（`DailyAmplitudeService`）
- **期望最新已收盘日期 `expected_closed_date(market, now)`**：
  - 根据市场时区与收盘时间推算理论最近一个工作日（A 股 15:00 后为当日，否则上一工作日；美股 16:00 ET 后为当日，否则上一工作日）。
  - 若 `cached.amplitude_as_of_date >= expected_closed_date`，直接判定新鲜，零网络开销。
- **节假日与失败冷却**：
  - 进程内维护 `_last_attempt: dict[str, datetime]`，同一证券在最近 30 分钟内已尝试拉取过（例如法定节假日导致 `as_of_date < expected_closed_date` 或上游超时）时，直接跳过重复请求，避免 10 秒轮询打爆上游。
- **并发与单飞**：
  - 按 `security_id` 粒度加锁防止并发重复拉取；批量刷新使用 `asyncio.Semaphore(4)` 限制并发数，单次请求超时沿用 `settings.quote_timeout_seconds`（默认 5s），整体刷新受 `asyncio.wait_for(..., timeout=4.0)` 保护。
  - 当 `quote_provider == "mock"` 或 `mode == "cache"` 时，仅读 `security_daily_metrics` 表，绝不触发网络请求。

## 4. API 契约变更

### 4.1 后端 Schema（`backend/app/api/schemas/dashboard.py`）
`DashboardPositionRead` 新增：
- `avg_amplitude_20d: Decimal | None = None` → JSON `avgAmplitude20d`
- `avg_amplitude_days: int | None = None` → JSON `avgAmplitudeDays`
- `avg_amplitude_as_of: str | None = None` → JSON `avgAmplitudeAsOf`

### 4.2 `DashboardService` 集成（`backend/app/services/dashboard.py`）
- `DashboardService.__init__` 接收可选 `amplitude_service: DailyAmplitudeService | None = None`（默认创建实例，并在 `collector.provider.name == "mock"` 时自动关闭网络刷新）。
- `DashboardService.build()` 中仅对当前账户活动持仓涉及的证券批量加载/按需刷新 20 日振幅映射 `dict[str, DailyAmplitudeSnapshot]`，传给 `_position_data()` 填充上述 3 个字段。

## 5. 前端改造设计

- **`frontend/src/api/types.ts`**：`DashboardPosition` 新增 `avgAmplitude20d?: string | null`、`avgAmplitudeDays?: number | null`、`avgAmplitudeAsOf?: string | null`。
- **`frontend/src/components/ActivePositionList.vue`**：
  - 表头 `.ap-th-prices` 保持原样（`现价 · 低批 · 上卖`），`PositionSortField` 不变。
  - `.ap-col-prices` 内在 `上卖` 行之后新增第 4 行 `.ap-price-row.ap-amplitude-row`：
    ```html
    <el-tooltip placement="top" :show-after="200">
      <template #content>
        <div class="ap-quote-tip">
          <strong>近 20 日平均振幅</strong>
          <span>{{ amplitudeTooltipText(row) }}</span>
        </div>
      </template>
      <div class="ap-price-row ap-amplitude-row">
        <span class="ap-price-label">20日振</span>
        <span class="ap-price-num ap-amplitude-spacer" aria-hidden="true" />
        <span class="ap-price-pct ap-amplitude-val">
          {{ formatPercent(row.avgAmplitude20d) }}
        </span>
      </div>
    </el-tooltip>
    ```
  - 样式：`.ap-amplitude-val` 使用中性色（`#475569`），字重 `600`，与上方涨跌幅列右对齐；PC、紧凑模式与 360px 移动端均复用 `.ap-price-row` 既有三栏网格与响应式字号，确保零横向溢出。

## 6. 兼容性与回滚

- **向后兼容**：新增表和新增可空响应字段完全向后兼容旧数据与现有接口消费者；本地 `ledger.db` 通过 Alembic 0007 无损升级。
- **回滚策略**：如需回滚，前端移除第 4 行、后端移除 `DashboardService` 调用即可，新增表可保留或执行 `alembic downgrade 0006_take_profit_notifications` 移除。
