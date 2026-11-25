# 执行计划：个股近 20 个交易日平均振幅

## 1. 实施步骤清单

1. **数据库模型与迁移**
   - 在 `backend/app/models/ledger.py` 新增 `SecurityDailyMetric` 模型并导出。
   - 新增 Alembic 迁移 `backend/alembic/versions/0007_security_daily_metrics.py` 创建 `security_daily_metrics` 表。
2. **日 K 线振幅拉取与缓存服务**
   - 新增 `backend/app/market_data/amplitude.py`，实现：
     - `DailyAmplitudeSnapshot`
     - `DailyAmplitudeProvider`（东财 A 股/美股日 K 主源 + 腾讯美股日 K 备源、有界响应、严格 `Decimal` 解析、排除当日未收盘、20 日均值计算）
     - `DailyAmplitudeService`（DB 批量读/写、交易日新鲜度判断、进程内失败/节假日冷却、并发受限单飞刷新、`mock` 模式离线保护）
   - 在 `backend/app/market_data/__init__.py` 导出所需类型。
3. **总览接口集成**
   - 在 `backend/app/api/schemas/dashboard.py` 的 `DashboardPositionRead` 新增 `avg_amplitude_20d`、`avg_amplitude_days`、`avg_amplitude_as_of`。
   - 在 `backend/app/services/dashboard.py` 接入 `DailyAmplitudeService`，在 `build()` 批量获取并在 `_position_data()` 填充字段。
4. **前端类型与组件展示**
   - 在 `frontend/src/api/types.ts` 的 `DashboardPosition` 新增 `avgAmplitude20d`、`avgAmplitudeDays`、`avgAmplitudeAsOf`。
   - 在 `frontend/src/components/ActivePositionList.vue` 的 `.ap-col-prices` 列 `上卖` 行下方新增 `20日振` 行与悬停说明 tooltip；不改动表头和排序逻辑。
5. **测试覆盖**
   - 后端单元测试：覆盖 A 股/美股日 K 解析、排除当日未收盘 K 线、不足 20 日样本、腾讯美股回退、`OF` 基金跳过、缓存命中与节假日冷却、异常降级。
   - 后端集成测试：验证 `GET /api/v1/dashboard` 返回 `avgAmplitude20d` / `avgAmplitudeDays` / `avgAmplitudeAsOf`，且 `mock` 模式不发公网请求。
   - 前端 E2E 测试：更新/补充 Playwright 用例验证活动仓位第 4 行 `20日振` 展示（正常值、缺失值 `—`、场外基金 `—`、表头不变、360px 移动端零横向溢出）。
6. **Spec 检查与更新**
   - 按 Phase 3.3 评估并更新 `.trellis/spec/frontend/active-position-dashboard.md` 与 `.trellis/spec/backend/market-data-guidelines.md`。

## 2. 验证命令

```bash
# 后端单元测试与集成测试
cd backend && uv run pytest

# 前端类型检查、构建与 E2E 测试
cd frontend && pnpm run type-check && pnpm run build
cd frontend && npx playwright test e2e/active_positions_last_sell.spec.ts e2e/mobile_layout.spec.ts
```

## 3. 风险点与回滚点

- **本地数据库保护**：严禁删除或清空本地 `ledger.db`，迁移 0007 仅做增量建表；测试统一使用临时 SQLite 数据库。
- **性能与上游限流**：必须在 `DailyAmplitudeService` 中落实按交易日缓存 + 失败/节假日冷却 + 并发上限与超时保护，避免影响 10s 总览轮询。
- **移动端 360px 布局**：新增第 4 行后需通过 Playwright 360px 视口断言确保 `scrollWidth <= clientWidth` 且数字对齐无折行。
