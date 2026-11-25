# 止盈提醒与企业微信 Webhook 推送技术设计

## 1. 架构总览与模块边界

本功能采用「行情流驱动 + 异步解耦评估 + 可插拔推送通道」三层结构，不耦合账务写路径，也不阻塞 `MarketDataHub` 的 SSE 实时广播：

```text
MarketDataHub.publish(snapshots)
    │  (非阻塞钩子：仅把变更符号集合投递到内存合并队列)
    ▼
ProfitAlertEngine (后台 asyncio 协程，按短防抖窗口如 0.5s 合并触发)
    ├─ 1. 过滤可触发行情（非过期 + 市场交易窗口/基金当日净值校验）
    ├─ 2. 短事务读取 OPEN 批次、三层有效止盈目标、用户启用通道与当日已送达记录
    ├─ 3. 计算达标项与 target_signature，跳过当日已送达项
    ├─ 4. 按 user_id 聚合为单轮通知卡片（按 4096 字节上限自动分批）
    ├─ 5. 异步线程调用 NotificationChannel.send(...)（最多 3 次退避重试）
    └─ 6. 仅对发送成功的项写入 profit_alert_deliveries（并兼容更新显式 ProfitTarget.status=TRIGGERED）
```

### 关键模块划分

| 模块路径 | 职责 |
| :--- | :--- |
| `backend/app/models/notification.py` | 新增 `NotificationChannel`、`ProfitAlertDelivery` 两个 ORM 模型（并在 `models/__init__.py` 导出）。 |
| `backend/alembic/versions/0006_take_profit_notifications.py` | 无损增量建表迁移（兼容 SQLite 与 MySQL `utf8mb4`）。 |
| `backend/app/services/notifications/channels.py` | 推送通道协议 `PushChannel`、企微 Webhook 校验/脱敏工具、`WeComWebhookChannel` 与通道注册工厂。 |
| `backend/app/services/notifications/formatter.py` | 企微 Markdown 卡片渲染（支持单条与多笔聚合、盘中估算标注、4096 字节安全分批）。 |
| `backend/app/services/notifications/service.py` | 用户通知配置 CRUD、脱敏序列化、测试推送发送。 |
| `backend/app/services/notifications/engine.py` | `ProfitAlertEngine`：挂载到 `MarketDataHub`，负责交易窗口判断、三层止盈评估、自然日去重、退避重试与成功落库。 |
| `backend/app/api/schemas/notification.py` | 通知配置读写与测试响应 Pydantic Schema。 |
| `backend/app/api/v1/notifications.py` | `/api/v1/notifications/wecom` 读取、保存、删除与测试推送路由（挂载到主 `router`）。 |
| `frontend/src/components/NotificationSettingDialog.vue` | 极简响应式推送设置弹窗（桌面端 + 移动端 `94vw` 零溢出）。 |

---

## 2. 数据库模型与迁移设计

### 2.1 `notification_channels`（用户通知渠道配置表）

预留 `channel_type` 以支持未来多渠道扩展，首期约束每个用户每种渠道唯一：

| 字段 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | `String(36)` | PK | UUID 主键 |
| `user_id` | `String(36)` | FK(`users.id`, `ON DELETE CASCADE`), NOT NULL | 归属用户 |
| `channel_type` | `String(32)` | NOT NULL, default `"wecom_webhook"` | 渠道类型标识 |
| `enabled` | `Boolean` | NOT NULL, default `False` | 是否启用推送 |
| `webhook_url` | `String(512)` | NULL | 企微群机器人 Webhook 地址 |
| `created_at` | `DateTime(timezone=True)` | NOT NULL | 创建时间（UTC） |
| `updated_at` | `DateTime(timezone=True)` | NOT NULL | 更新时间（UTC） |

- 唯一约束：`UniqueConstraint("user_id", "channel_type", name="uq_notification_channels_user_type")`
- 索引：`Index("ix_notification_channels_enabled", "channel_type", "enabled")`

### 2.2 `profit_alert_deliveries`（止盈推送自然日去重表）

仅在外部渠道**确认发送成功**后写入，用作自然日防重与目标变更重触发判定：

| 字段 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | `String(36)` | PK | UUID 主键 |
| `user_id` | `String(36)` | FK(`users.id`, `ON DELETE CASCADE`), NOT NULL | 归属用户 |
| `lot_id` | `String(36)` | FK(`lots.id`, `ON DELETE CASCADE`), NOT NULL | 对应持仓批次 |
| `target_key` | `String(64)` | NOT NULL | 目标键：显式目标为 `LOT:{target_id}`；继承目标为 `INHERITED` |
| `target_signature` | `String(128)` | NOT NULL | 目标指纹：`{source}\|{basis}\|{target_return_pct:.6f}\|{target_price:.6f}\|{planned_qty:.4f}` |
| `trigger_date` | `String(10)` | NOT NULL | 北京时间自然日 `YYYY-MM-DD` |
| `channel_type` | `String(32)` | NOT NULL | 送达渠道类型 |
| `triggered_price` | `ExactDecimal(6)` | NOT NULL | 触发时最新价/估算净值 |
| `target_price` | `ExactDecimal(6)` | NOT NULL | 触发时目标价 |
| `is_estimated` | `Boolean` | NOT NULL, default `False` | 是否由场外基金盘中估算触发 |
| `sent_at` | `DateTime(timezone=True)` | NOT NULL | 实际发送成功时间（UTC） |

- 唯一约束：`UniqueConstraint("user_id", "lot_id", "target_key", "target_signature", "trigger_date", "channel_type", name="uq_profit_alert_deliveries_dedup")`
- 索引：`Index("ix_profit_alert_deliveries_user_date", "user_id", "trigger_date")`

> **设计要点**：
> 1. 当用户当天修改某批次目标收益率或修改全局/个股止盈率时，`target_signature` 必然变化，从而天然满足「当天改目标后重新触发一次」，而无需手动清理历史记录。
> 2. 迁移脚本 `0006_take_profit_notifications.py`（`down_revision = "0005_security_proxy_fields"`）在建表前检查 `inspector.get_table_names()` 保证幂等，兼容 SQLite 与 MySQL，且遵循 `database-guidelines.md` 不在 SQL 层比较 `ExactDecimal` 文本列。

---

## 3. 核心算法与边界规则

### 3.1 企微 Webhook 校验与脱敏契约
- **合法性校验**：
  - 仅允许 `https` 协议，域名严格等于 `qyapi.weixin.qq.com`（防止 SSRF 访问内网地址），路径为 `/cgi-bin/webhook/send`，且查询参数包含非空 `key`（长度限制合理范围，拒绝空白与额外危险字符）。
- **脱敏规则**：
  - `mask_wecom_webhook(url)` 将 `key=abcdef1234567890` 脱敏为 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcd****7890`（若 key 长度 ≤ 8 则全替换为 `****`）。
  - 所有日志、异常信息与 REST 响应均只输出脱敏 URL。
- **发送实现**：
  - 使用 Python 标准库 `urllib.request.Request` POST JSON `{"msgtype": "markdown", "markdown": {"content": content}}`，默认超时 5 秒（可通过配置调整）。
  - 解析响应体 JSON：仅当 HTTP 200 且 `errcode == 0` 视为成功；否则抛出带 `errcode` / `errmsg` 的 `NotificationDeliveryError`（错误码 `45009` 记为限频可重试）。

### 3.2 行情可触发窗口判定
在 `ProfitAlertEngine` 中对每只变更证券的 `QuoteSnapshot` 判定是否允许参与止盈评估：
1. 若 `snapshot.is_stale` 为 `True` 或 `snapshot.last_price <= 0`，直接跳过。
2. 市场为 `SH` / `SZ` / `BJ`：`quote_service.calendar.is_session_open(now)` 必须为 `True`。
3. 市场为 `US`：`quote_service.us_calendar.is_session_open(now)` 必须为 `True`（复用现有美股日历，覆盖盘前/常规/盘后）。
4. 市场为 `OF`（场外基金）：
   - `snapshot.is_estimated == True`（绑定了代理标的）：要求 `snapshot.market_session not in (None, "closed")`（即代理标的处于交易时段）。
   - `snapshot.is_estimated == False`（官方净值）：要求 `snapshot.source_time` 非空，且转换为北京时间后的日期 `== now.astimezone(BEIJING_TZ).date()`（仅当日新公布的官方净值触发，避免休市或次日冷启动重复触发）。

### 3.3 三层止盈目标解析（统一评估口径）
针对变更证券下所有 `Lot.status == "OPEN"` 且 `lot.remaining_qty > 0`（Python 过滤）的批次：
1. 筛出批次下所有有效显式目标：`target.status in {"ACTIVE", "TRIGGERED"}` 且 `target.planned_sell_qty > 0`。
2. **存在有效显式目标时**：
   - 不继承 `SECURITY`/`GLOBAL`（与现有 `ProfitSettingsService` 语义一致）。
   - 对每一条 `alert_enabled == True` 且 `target_price_estimate is not None` 的显式分段目标独立评估：若 `snapshot.last_price >= target.target_price_estimate`，构造命中项（`target_key = f"LOT:{target.id}"`）。
3. **无有效显式目标时**：
   - 按 `ProfitSettingsService.scope_key(lot.security_id, user_id=owner_user_id)` → `ProfitSettingsService.scope_key(user_id=owner_user_id)` 查找继承设置。
   - 若存在且 `setting.alert_enabled == True`，调用 `TargetService.solve_price(unit_cost=lot.unit_cost, planned_sell_qty=lot.remaining_qty, target_return_pct=setting.target_return_pct, basis=setting.calculation_basis)` 计算目标价；若 `snapshot.last_price >= target_price`，构造命中项（`target_key = "INHERITED"`）。
4. 计算展示指标（全 `Decimal`）：
   - `current_return_pct = (last_price - lot.unit_cost) / lot.unit_cost`（`unit_cost > 0` 时）。
   - `estimated_pnl = round_amount((last_price - lot.unit_cost) * planned_qty)`。

### 3.4 聚合、重试与并发安全
- **防抖合并**：`MarketDataHub.publish()` 同步记录变更符号并触发 `engine.notify_quotes_updated(symbols)`（纯内存非阻塞）；引擎侧按 `0.5s` 合并窗口唤醒一次批量评估，避免长桥高频 tick 反复扫库。
- **单轮聚合与分批**：同一用户的多个命中项合并为一条 Markdown 卡片（头部展示触发时间与命中笔数，逐笔列出标的、账户、成本/现价/目标价、当前/目标收益率、预计浮盈及是否盘中估算）。若 UTF-8 字节长度超过企微限制（按安全阈值 3800 字节），自动拆分为多条有序卡片依次发送。
- **重试策略**：网络错误、超时或可重试企微错误（如限频）最多尝试 3 次，采用短退避（如 `1s`、`3s`）；3 次均失败则记录脱敏错误日志并放弃本轮，不写入去重表，同时对该用户通道在内存中记录短暂熔断（如 60 秒）防止高频行情下持续打挂或刷屏报错日志。
- **事务边界**：读取评估与成功落库分开短事务执行（网络 I/O 绝不持有 SQLite 数据库连接或写锁）；写入 `profit_alert_deliveries` 时使用幂等容错（遇到唯一冲突直接跳过），并同步更新首次触发的显式 `ProfitTarget.status = "TRIGGERED"` / `triggered_at`。

---

## 4. REST API 契约

路由前缀：`/api/v1/notifications`，全部接口要求登录鉴权（`Depends(get_current_user)`）。

### 4.1 `GET /api/v1/notifications/wecom`
返回当前用户的企微 Webhook 配置（脱敏）：
```json
{
  "channelType": "wecom_webhook",
  "enabled": true,
  "configured": true,
  "maskedWebhookUrl": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcd****7890",
  "updatedAt": "2026-11-25T16:40:00+08:00"
}
```
未配置时返回 `enabled: false, configured: false, maskedWebhookUrl: null, updatedAt: null`。

### 4.2 `PUT /api/v1/notifications/wecom`
保存或更新当前用户企微配置：
- 请求体：`{ "enabled": boolean, "webhookUrl"?: string | null }`
  - `webhookUrl` 省略（unset）：保留数据库现有 Webhook（支持仅切换 `enabled` 开关）。
  - `webhookUrl` 为非空字符串：校验企微 Webhook 合法性后覆盖保存。
  - `webhookUrl` 为 `null` 或空字符串 `""`：清空已保存 Webhook，并自动将 `enabled` 置为 `false`。
  - 若请求 `enabled: true` 但最终无有效 Webhook，返回 `422 VALIDATION_ERROR`。
- 响应：同 `GET`，返回最新脱敏配置。

### 4.3 `POST /api/v1/notifications/wecom/test`
向当前用户已保存的企微 Webhook（或请求体可选临时覆盖 `webhookUrl` 以便保存前测通？为保持极简且避免 SSRF 滥用，优先支持：若请求体带非空 `webhookUrl` 需同样严格校验白名单域名后测试，否则使用当前用户已保存的 Webhook）发送一条测试 Markdown 消息：
- 成功返回 `200`：`{ "ok": true, "message": "测试消息已发送至企业微信群" }`
- 未配置 Webhook 返回 `422 VALIDATION_ERROR`。
- 上游网络或企微返回非 0 `errcode` 返回 `502`（或统一 DomainError `NOTIFICATION_DELIVERY_FAILED`），附带明确可读失败原因（脱敏）。

---

## 5. 前端交互设计

- **新增组件**：`frontend/src/components/NotificationSettingDialog.vue`
  - 遵循 `ProfitSettingDialog.vue` 的代次防竞态与并发锁模式，适配 `useViewport().isMobile`：移动端 `width="94vw"`、`label-position="top"`，保证 `360x780` 与 `390x844` 零横向溢出。
  - 表单项：
    1. 启用止盈推送（`el-switch`）
    2. 企业微信 Webhook 地址（`el-input`，已配置时下方展示当前已保存的脱敏地址，输入新地址覆盖、留空保留原值）
  - 底部按钮：`清除配置`、`发送测试`、`取消`、`保存设置`。
- **入口挂载**：[DashboardPage.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/pages/DashboardPage.vue)
  - 桌面端工具栏在「全局止盈设置」旁增加「推送设置」按钮。
  - 移动端工具栏在「全局止盈」旁增加紧凑按钮「推送」，确保两行/单行自适应且零横向溢出。
- **API 客户端与类型**：在 `frontend/src/api/types.ts` 与 `frontend/src/api/client.ts` 中新增 `WeComNotificationSetting`、`WeComNotificationUpdatePayload`、`WeComNotificationTestResult` 及对应请求方法。

---

## 6. 运维、兼容与回滚说明

- **历史数据兼容**：仅新增两张独立表，不修改现有交易/批次/止盈表结构；已有 `ledger.db` 启动时由 `prepare_database_schema` 自动执行 `0006` 迁移，不丢失任何记录。
- **生命周期管理**：在 `backend/app/main.py` 的 `lifespan` 中随 `quote_hub.start()` 启动 `profit_alert_engine.start()`，停机时先停止 `profit_alert_engine.stop()` 再关闭 `quote_hub.stop()`，避免停机挂起。
- **优雅降级**：若 `quote_provider == "mock"` 或无任何用户启用通知，评估引擎保持零外部请求开销；单次发送异常完整捕获，不影响行情推流与核心记账。
- **回滚方案**：若需回滚，停止服务后执行 `alembic downgrade 0005_security_proxy_fields`（仅删除新增两张通知表）并还原代码提交，零影响既有账务数据。
