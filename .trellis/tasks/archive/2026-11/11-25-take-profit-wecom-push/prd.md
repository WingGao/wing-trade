# 止盈提醒与企业微信 Webhook 推送 PRD

## 1. 目标与用户价值

为自托管交易账本增加**盘中实时止盈提醒推送**能力，首期接入**企业微信群机器人 Webhook**，借助荣耀手机（Android / MagicOS）中企业微信已集成的官方系统级推送通道，实现低能耗、杀后台可达的止盈触达；同时后端建立可扩展的通知通道抽象，便于未来按需接入其他推送渠道（如飞书、钉钉或自建 Android 壳直连荣耀 Push Kit）。

## 2. 现状与已确认事实

- **项目形态**：纯自托管 Web 应用（FastAPI 后端 + Vue 3 前端，无原生 Android 客户端），支持 SQLite / MySQL 双数据库及多用户登录隔离（[auth.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/models/auth.py)）。
- **实时行情源已就绪**：[hub.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/hub.py) 中的 `MarketDataHub` 随应用常驻运行，支持长桥实时推流与东财定时轮询，并在内存中维护持仓/自选/场外基金代理的实时有效报价（含场外基金绑定 ETF 代理后的盘中估算净值）。
- **三层止盈模型已存在**：
  - 批次显式分段目标 `ProfitTarget` 与用户级个股/全局默认 `ProfitSetting` 均已包含 `alert_enabled` 字段（[ledger.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/models/ledger.py#L224-L263)）。
  - 继承解析优先级为 `LOT > SECURITY > GLOBAL`（[profit_settings.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/services/profit_settings.py#L29-L77)）。
  - 现有 [service.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/service.py#L334-L355) 中的 `evaluate_targets` 仅扫描显式 `ProfitTarget`，且 `MarketDataHub.flush_dirty_to_db`（[hub.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/hub.py#L659-L680)）显式传入 `evaluate_targets=False`，尚未驱动后台自动止盈检测与外部推送。
- **交易日历现状**：[calendar.py](file:///media/wing/Bt/WingProjs/wing-trade/backend/app/market_data/calendar.py) 中，A 股/美股休市时 `closed_ttl_seconds = 3600`，仅判断 `is_stale=False` 不足以排除休市拉取基线行情带来的误触发；场外基金 `OpenFundCalendar.is_session_open()` 恒为 `False`，其盘中估算与官方净值需分别处理。
- **前端入口位置**：[DashboardPage.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/pages/DashboardPage.vue#L447-L455)（PC 工具栏）与 [DashboardPage.vue](file:///media/wing/Bt/WingProjs/wing-trade/frontend/src/pages/DashboardPage.vue#L545-L552)（移动端工具栏）已有「全局止盈设置」入口，适合就近放置「推送设置」弹窗入口，且需满足移动端 `360px ~ 430px` 零横向溢出契约。
- **运行时依赖与数据安全约束**：后端生产依赖无第三方 HTTP 客户端（标准库 `urllib.request` 可直接发请求）；本地 `ledger.db` 严禁删除或清空，表结构变更必须通过新增 Alembic 迁移无损完成。

## 3. 核心需求

### REQ-1 可扩展推送通道与企业微信 Webhook 实现
- 后端抽象统一的通知发送接口（`NotificationChannel`），将止盈评估引擎与具体推送渠道解耦。
- 首期实现 `wecom_webhook`（企业微信群机器人 Webhook）：
  - 仅接受合法的企业微信群机器人 Webhook URL（HTTPS，主机为 `qyapi.weixin.qq.com`，路径为 `/cgi-bin/webhook/send` 且包含非空 `key` 查询参数）。
  - 所有 API 响应与服务端日志严禁泄露完整 `key`，必须脱敏展示（如仅保留前 4 位与后 4 位）。
  - 使用企微 `markdown` 消息格式，内容包含：账户名称、证券名称与规范代码、买入批次成本价与计划/剩余数量、当前价格（场外基金估算净值须明确标注「盘中估算」）、目标价格、当前收益率与目标收益率、预计浮盈金额与币种。

### REQ-2 按用户隔离的通知配置与一键测试
- 持久化按 `user_id` 隔离的通知渠道配置（支持 `channel_type`、`enabled`、`webhook_url`、更新时间戳）。
- 提供当前登录用户的配置读写与测试接口：
  - 读取当前用户配置（未配置时返回默认关闭状态；已配置时返回脱敏后的 Webhook 与配置状态）。
  - 保存配置（支持仅开关启用状态而不重填已有 Webhook、更新 Webhook，或显式清空配置）。
  - 一键发送测试消息：校验已配置的企微 Webhook 真实发送一条中文测试卡片，返回明确成功或失败原因（含企微非 0 `errcode` 提示）。
- 严格用户隔离：任意用户只能读取、修改和测试自己的通知配置，无法越权访问他人配置。

### REQ-3 实时三层止盈检测引擎
- 将止盈检测接入 `MarketDataHub` 实时报价发布流（长桥实时推流与东财后台轮询），采用异步解耦队列执行，绝不阻塞 SSE 广播与记账请求。
- 评估范围覆盖所有持有中批次（`Lot.status == "OPEN"` 且 `remaining_qty > 0`），按既定三层继承优先级生效：
  1. 若批次存在至少一条有效显式分段目标（`ProfitTarget.status in {"ACTIVE", "TRIGGERED"}` 且 `planned_sell_qty > 0`），逐条评估其中 `alert_enabled == True` 且 `target_price_estimate` 非空的目标。
  2. 若批次无有效显式目标，则按 `SECURITY > GLOBAL` 继承 `ProfitSetting`；当继承设置存在且 `alert_enabled == True` 时，使用 `TargetService.solve_price` 计算目标价并参与评估。
- 有效行情与交易窗口约束：
  - 忽略 `is_stale == True` 或 `last_price <= 0` 的报价。
  - A 股（`SH`/`SZ`/`BJ`）与美股（`US`）仅在对应市场活跃交易时段（`calendar.is_session_open(now)`）内评估触发，防止休市基线拉取误推。
  - 场外基金（`OF`）：
    - 盘中估算净值（`is_estimated == True`）：仅在参考标的处于活跃交易时段（`market_session != "closed"`）且未过期时评估。
    - 官方净值（`is_estimated == False`）：仅当官方净值日期（`source_time` 转北京时间自然日）等于当天北京时间日期且未过期时评估，防止非交易日或跨日重启重复误推。
- 兼容现有显式目标状态：当显式 `ProfitTarget` 从 `ACTIVE` 首次达标时，同步更新 `status = "TRIGGERED"` 与 `triggered_at`，且不阻碍次日按自然日去重规则继续提醒。

### REQ-4 自然日去重、目标变更重触发、同轮聚合与失败重试
- **自然日去重**：按北京时间自然日（`YYYY-MM-DD`）持久化成功送达记录，去重键绑定 `(user_id, lot_id, target_key, target_signature, trigger_date)`：
  - 同一批次同一目标在同一自然日内最多成功推送 1 次。
  - 若用户当天修改目标收益率或目标价，导致 `target_signature` 变化，视为新目标可在当日重新触发一次。
  - 若次日批次仍持有且仍达标，次日交易时段可再次触发提醒。
- **同轮聚合**：同一轮行情触发中，同一用户命中的多笔止盈项合并为一条企微 Markdown 汇总卡片发送（超过企微单条 Markdown 4096 字节上限时自动按条拆批发送），防刷屏并规避每分钟 20 条限频。
- **失败不消耗去重名额与有限重试**：网络异常、超时或企微返回非 0 `errcode` 时，仅记录脱敏错误日志，不写入“当日已送达”标记；采用有限次退避重试（最多 3 次），超过重试上限后记录告警并等待后续冷却或下一次有效触发周期，不无限死循环重试。

### REQ-5 前端极简推送设置弹窗
- 在 `DashboardPage` 的 PC 工具栏与移动端工具栏就近提供「推送设置」入口，打开响应式弹窗（桌面端常规宽度，移动端 `width="94vw"`、`label-position="top"`，满足 `360x780` 与 `390x844` 零横向溢出）。
- 弹窗功能保持极简：
  - 启用止盈推送总开关。
  - 企业微信群机器人 Webhook 输入框（已配置时展示脱敏占位/当前脱敏值，留空提交保留原密钥；提供清除配置操作）。
  - 保存设置、发送测试推送按钮与明确的成功/失败反馈。

## 4. 不在范围内（Out of Scope）

- 前端推送历史记录列表页与查询接口（发送记录仅写后端日志与内部去重表）。
- 企微通知卡片中的外部账本跳转链接及外网域名配置。
- 自建 Android 壳 App、Honor Push Kit SDK 直连，以及飞书/钉钉/Server酱/邮件止盈推送（仅预留通道扩展接口）。
- 自动下单或修改任何账务交易事实。

## 5. 验收标准（Acceptance Criteria）

- **AC-1（对应 REQ-1、REQ-2、REQ-5）**：用户可在前端打开「推送设置」弹窗，保存合法企微 Webhook 并开启推送开关；重新打开弹窗时仅显示脱敏后的 Webhook；点击「发送测试」成功时企微群收到中文测试卡片，传入非法 URL 或企微返回错误码时页面给出清晰报错。
- **AC-2（对应 REQ-2）**：用户 A 与用户 B 的通知配置完全隔离，用户 A 的止盈命中仅推送至用户 A 的 Webhook，且无法读取或修改用户 B 的配置。
- **AC-3（对应 REQ-3）**：盘中收到有效实时行情且达到目标价时，批次显式目标、个股继承目标、全局继承目标（`alertEnabled=true`）均可触发推送；`alertEnabled=false`、过期行情（`is_stale=true`）以及休市基线拉取均不触发推送。
- **AC-4（对应 REQ-3）**：场外基金绑定参考标的且盘中估算净值达标时触发推送，卡片中明确标注「盘中估算」；未绑定代理的场外基金仅在当日官方净值更新且达标时触发。
- **AC-5（对应 REQ-4）**：同一批次同一目标当日多次穿越目标价只成功推送 1 次；当日修改目标收益率/目标价后可重新触发；次日仍达标时再次触发；同轮命中多笔目标时自动合并为一条汇总卡片（超长自动分批）。
- **AC-6（对应 REQ-4）**：Webhook 发送失败时记录脱敏日志、不阻塞行情推流与记账、不消耗当日去重名额，并按上限重试后停止。
- **AC-7（迁移与质量门禁）**：Alembic `0006` 迁移在临时 SQLite 库 `upgrade head` / `downgrade` 无损通过且不触碰本地 `ledger.db`；后端 `pytest`、`ruff`、`mypy` 与前端 `type-check`、`build`、移动端及新增 E2E 全部通过。
