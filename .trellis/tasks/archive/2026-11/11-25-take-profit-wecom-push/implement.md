# 止盈提醒与企业微信 Webhook 推送实施计划

## 1. 有序实施清单

### 阶段 A：数据模型与无损迁移
1. 新增 `backend/app/models/notification.py`，定义 `NotificationChannel` 与 `ProfitAlertDelivery`（含完整中文注释、显式约束与索引名），并在 `backend/app/models/__init__.py` 导出。
2. 新增 Alembic 迁移 `backend/alembic/versions/0006_take_profit_notifications.py`（`down_revision = "0005_security_proxy_fields"`），实现幂等 `upgrade` 与 `downgrade`，兼容 SQLite 与 MySQL。
3. 编写迁移单测/集成测试（使用临时 SQLite 库，严禁触碰根目录 `ledger.db`），验证 `upgrade head` → `downgrade 0005_security_proxy_fields` → `upgrade head`。

### 阶段 B：通知通道抽象与企微 Webhook 实现
4. 新增 `backend/app/services/notifications/__init__.py`、`channels.py`、`formatter.py`：
   - 定义 `PushChannel` 协议、`NotificationDeliveryError`、`validate_wecom_webhook_url`、`mask_wecom_webhook_url`。
   - 实现 `WeComWebhookChannel`（标准库 `urllib.request`，可注入 transport 以便离线单测，校验 HTTP 状态与企微 `errcode == 0`）。
   - 实现 `format_take_profit_markdown` 与 `format_test_markdown`（支持单笔/多笔聚合、盘中估算标注与 3800 字节安全分批）。
5. 编写单元测试 `backend/tests/unit/test_wecom_notifications.py`，覆盖 URL 白名单校验（防 SSRF）、密钥脱敏、Markdown 格式化、估算标注、超长分批以及企微错误码处理。

### 阶段 C：配置服务与 REST API
6. 新增 `backend/app/services/notifications/service.py`、`backend/app/api/schemas/notification.py`、`backend/app/api/v1/notifications.py`，并在 `backend/app/api/v1/router.py` 注册路由，补充对应错误码映射（`backend/app/api/errors.py` 如需要）。
7. 实现 `GET /api/v1/notifications/wecom`、`PUT /api/v1/notifications/wecom`、`POST /api/v1/notifications/wecom/test`，严格按 `current_user.id` 隔离。

### 阶段 D：实时止盈评估引擎与 `MarketDataHub` 集成
8. 新增 `backend/app/services/notifications/engine.py` 实现 `ProfitAlertEngine`：
   - 交易窗口校验（A 股/美股交易时段、场外基金盘中估算窗口、场外基金当日官方净值校验、拒绝 `is_stale` 与非正价）。
   - 三层止盈继承评估（显式多段 `ProfitTarget` > 个股 `ProfitSetting` > 全局 `ProfitSetting`，仅评估 `OPEN` 且 `remaining_qty > 0` 且 `alert_enabled=True`）。
   - 北京时间自然日去重、目标指纹变更重触发、同轮按用户聚合、最多 3 次退避重试与通道短暂熔断、成功后写去重表并兼容更新首次触发显式 `ProfitTarget.status = "TRIGGERED"`。
9. 在 `backend/app/market_data/hub.py` 增加非阻塞行情发布监听回调机制（`add_quote_listener` / `on_publish`），在 `backend/app/api/v1/router.py` 与 `backend/app/main.py` 完成 `ProfitAlertEngine` 装配与 lifespan 启停管理。
10. 编写集成测试 `backend/tests/integration/test_profit_alert_notifications.py`：
    - 覆盖多用户配置隔离、脱敏返回、仅切开关保留原 URL、清空配置、测试推送成功/失败。
    - 覆盖显式分段/个股继承/全局继承三层触发、场外基金盘中估算与当日官方净值触发、过期/休市/`alertEnabled=false` 不触发。
    - 覆盖当日去重、同日改目标重触发、跨日再次触发、同轮多笔合并卡片、发送失败不消耗去重名额及有限次重试后停止。

### 阶段 E：前端极简配置弹窗与双端适配
11. 更新 `frontend/src/api/types.ts` 与 `frontend/src/api/client.ts`，补充企微推送配置读写与测试接口。
12. 新增 `frontend/src/components/NotificationSettingDialog.vue`，并在 `frontend/src/pages/DashboardPage.vue` 桌面端与移动端工具栏接入「推送设置」弹窗。
13. 补充前端 Playwright E2E 测试（mocked API，覆盖桌面端保存/脱敏/一键测试/清除，以及 `390x844` / `360x780` 移动端打开弹窗零横向溢出）。

---

## 2. 验证命令清单

```bash
# 1. 后端编译、风格与类型检查
uv run --cache-dir .uv-cache --project backend python -m compileall -q backend/app backend/tests
uv run --cache-dir .uv-cache --project backend ruff check backend/app backend/tests
uv run --cache-dir .uv-cache --project backend mypy backend/app

# 2. 后端单元与集成测试（隔离临时库，不修改本地 ledger.db）
uv run --cache-dir .uv-cache --project backend pytest backend/tests

# 3. 前端类型检查、生产构建与 E2E 测试
pnpm --dir frontend run type-check
pnpm --dir frontend run build
pnpm --dir frontend exec playwright test
```

---

## 3. 高风险文件与回滚点

| 风险点 | 涉及文件 | 防护与回滚措施 |
| :--- | :--- | :--- |
| 行情推流热路径阻塞或异常 | `backend/app/market_data/hub.py`、`backend/app/main.py` | 监听器仅做内存入队，评估与网络发送全在独立后台协程执行，所有异常捕获记日志；随时可移除监听器回退。 |
| SQLite 并发写锁争用 | `backend/app/services/notifications/engine.py` | 网络请求期间绝不持有数据库会话；读写分成极短事务，并容忍唯一键并发冲突。 |
| 密钥泄露到日志或前端 | `channels.py`、`service.py`、`notifications.py` | 统一经 `mask_wecom_webhook_url` 脱敏，单测显式断言日志与 API 响应均不含原始 key。 |
| 移动端横向溢出或既有 E2E 冲突 | `DashboardPage.vue`、`NotificationSettingDialog.vue` | 弹窗使用 `width="94vw"` 与 `label-position="top"`，按钮不破坏原有顶栏标题与选择器，并通过 `mobile_layout.spec.ts` 全量回归验证。 |
| 数据库迁移安全 | `0006_take_profit_notifications.py` | 仅新增两张独立表，支持 `downgrade 0005_security_proxy_fields` 无损回滚，绝不操作真实 `ledger.db`。 |

---

## 4. 启动实现前检查

- [x] `prd.md` 已完成收敛并检查无重复/无阻塞开放问题
- [x] `design.md` 已覆盖表结构、迁移、触发窗口、去重与 API 契约
- [x] `implement.md` 已明确实施顺序、验证命令与风险回滚点
- [ ] 等待用户对最终规划摘要明确批准后，再执行 `task.py start` 进入 Phase 2 实现
