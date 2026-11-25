# 实施计划：前后端时间默认为北京时间（强时区统一处理）

## 实施清单（按顺序执行）

### 阶段一：后端统一时区模块与强时区入参/出参
1. **新增后端集中式时间模块**：
   - 新建 `backend/app/domain/time.py`，实现 `BEIJING_TZ`、`now_utc`、`now_beijing`、`assume_utc`、`require_aware_utc`、`to_beijing`、`format_beijing_iso`，所有方法均写中文注释。
   - 更新 `backend/app/market_data/calendar.py` 复用 `BEIJING_TZ`。
2. **统一后端强时区入参校验**：
   - 更新 `backend/app/api/schemas/common.py`：定义公共类型 `AwareDateTime = Annotated[datetime, AfterValidator(require_aware_utc)]`。
   - 更新 `backend/app/api/schemas/trade.py`：`BuyTradeRequest`、`SellPreviewRequest`/`SellTradeRequest`、`TradeUpdateRequest` 的 `executed_at` 统一使用 `AwareDateTime`，拒绝无时区时间入参。
   - 更新 `backend/app/services/ledger.py`：`buy`、`sell`、`edit_trade` 统一使用 `assume_utc` 规范化并保证 UTC 落库与精准编辑快照对比。
3. **统一后端 REST 与 SSE 输出为北京时间（`+08:00`）**：
   - 更新 `backend/app/api/v1/router.py`：统一用 `format_beijing_iso` 替换 `_dt` / `_dt_utc` 及 `quote_data` 时间序列化。
   - 更新 `backend/app/api/v1/auth.py`：用 `format_beijing_iso` 替换 `_dt_utc`。
   - 更新 `backend/app/api/v1/market.py`：行情与分时点 `sourceTime`、`fetchedAt`、`points[].time` 统一用 `format_beijing_iso`。
   - 更新 `backend/app/market_data/hub.py`：`_fmt_dt` 委托 `format_beijing_iso`。
4. **后端日志与容器默认北京时间**：
   - 更新 `backend/app/main.py`：`configure_logging` 配置北京时间日志格式器。
   - 更新 `Dockerfile`、`backend/Dockerfile`、`compose.yaml`：默认设置 `TZ=Asia/Shanghai`。

### 阶段二：前端统一时间模块与组件改造
5. **新增前端集中式时间模块**：
   - 新建 `frontend/src/utils/time.ts`，实现 `BEIJING_TIMEZONE`、`BEIJING_OFFSET`、`hasTimezoneOffset`、`nowBeijingPickerValue`、`toAwareBeijingIso`、`requireTimezoneIso`、`toBeijingIsoString`、`formatDateTime`、`formatBeijingAxisTime`，所有方法均写中文注释。
   - 更新 `frontend/src/utils/format.ts`：重新导出 `formatDateTime` 等方法，保持既有导入兼容。
6. **改造前端录入、编辑与展示组件**：
   - 更新 `frontend/src/components/BuyTradeDialog.vue`：默认与每次打开刷新为 `nowBeijingPickerValue()`，提交经由 `toAwareBeijingIso()`。
   - 更新 `frontend/src/components/SellMatchDrawer.vue`：默认与每次打开刷新为 `nowBeijingPickerValue()`，预览与提交经由 `toAwareBeijingIso()`。
   - 更新 `frontend/src/components/EditTradeDialog.vue`：预填用 `toBeijingIsoString()` 展示 `+08:00`（保留微秒），提交经由 `requireTimezoneIso()` 强校验必须带时区。
   - 更新 `frontend/src/components/IntradayChart.vue`：横轴与悬浮提示使用 `formatBeijingAxisTime()`。
   - 更新 `frontend/src/pages/AdminUsersPage.vue`：复用统一 `formatDateTime()`。

### 阶段三：测试补齐与全量验证
7. **后端测试**：
   - 新增 `backend/tests/unit/test_time.py`（覆盖 `require_aware_utc` 拒绝无时区/转换带时区、`format_beijing_iso` 统一输出 `+08:00` 与微秒保留、`assume_utc` 兼容历史 naive UTC 记录）。
   - 在接口测试中覆盖：无时区 `executedAt` 返回 422、带时区入参正确换算并回显 `+08:00`、SSE/行情/认证接口均输出 `+08:00`。
   - 运行全量 pytest，按新契约更新受影响的旧测试断言。
8. **前端检查与测试**：
   - 运行 `pnpm --dir frontend run type-check` 与 `pnpm --dir frontend run build`，并在 `TZ=UTC` 下验证时间工具函数行为。

## 验证命令

```bash
# 后端编译与全量测试
uv run --cache-dir .uv-cache --project backend python -m compileall -q backend/app backend/tests
uv run --cache-dir .uv-cache --project backend pytest

# 前端类型检查与构建
pnpm --dir frontend run type-check
pnpm --dir frontend run build
```

## 高风险文件与回滚点

- `backend/app/api/schemas/common.py` / `trade.py`：引入强时区入参校验 `AwareDateTime`，需同步排查所有后端测试中提交的 `executedAt` 是否已带时区。
- `frontend/src/components/EditTradeDialog.vue`：微秒保留与未修改检测逻辑，需验证无多余 `revision` 递增。
- 回滚方式：不涉及数据库 schema/数据变更，直接 `git revert` 即可无损回滚。
