# 技术设计：用户体系与邮箱验证码登录

## 1. 架构概览与模块边界

在现有分层架构下新增认证与用户管理域，保持账务核心领域（`AccountingService`、`MatchingService`、`CycleService`、`TradeLedgerService`）不感知 HTTP 会话细节，仅通过显式 `user_id` / `account_id` 边界隔离：

- **配置层**（`backend/app/config.py`）：集中解析认证、默认管理员与邮件发送环境变量。
- **数据模型层**（`backend/app/models/auth.py` + `backend/app/models/ledger.py`）：
  - 新增 `User`、`EmailVerificationCode`、`UserSession` 三张认证表。
  - `Account` 新增 `user_id` 外键指向 `users.id`。
  - `ProfitSetting` 改为按 `user_id` 维度隔离（`scope_key` 包含用户维度并新增 `user_id` 外键与 `(user_id, security_id)` 唯一约束）。
- **迁移与启动初始化层**（`backend/alembic/versions/0004_user_auth_system.py` + `backend/app/db/migrations.py` + `backend/app/services/auth_bootstrap.py`）：
  - Alembic `0004` 迁移兼容 SQLite（`batch_alter_table`）和 MySQL。
  - 修复旧版无 `alembic_version` 的 SQLite 存量库升级路径：先识别已存在表状态再 `stamp` 到对应的历史版本（如 `0003`）然后 `upgrade head`，严禁对已有表做破坏性重建或清空 `ledger.db`。
  - 启动阶段调用 `ensure_default_admin_and_backfill()` 确保默认管理员存在，并将所有 `user_id IS NULL` 的存量 `accounts` 与 `profit_settings` 回填归属默认管理员。
- **服务层**：
  - `backend/app/services/email.py`：抽象 `EmailSender` 接口，提供 `ConsoleEmailSender`（日志输出）与 `SmtpEmailSender`（标准库 `smtplib`）。
  - `backend/app/services/auth.py`：`AuthService` 负责邮箱规范化、验证码生成/哈希/限流/校验、首登激活与默认账户创建、会话签发/校验/吊销、管理员邀请与禁用启用。
- **API 与依赖注入层**：
  - `backend/app/api/deps.py`：新增 `get_current_user`、`require_admin` 依赖；重构 `resolve_account(session, user, account_id)`、`resolve_user_trade`、`resolve_user_lot`、`resolve_user_target`，统一实施用户归属校验。
  - `backend/app/api/v1/auth.py`：登录发码、验证码登录、当前用户、退出登录接口。
  - `backend/app/api/v1/admin.py`：管理员用户/邀请列表、按邮箱邀请、撤销未激活邀请、禁用/启用用户接口。
  - `backend/app/api/v1/router.py` 与 `backend/app/api/v1/market.py`：接入 `get_current_user`，全部业务接口按当前用户隔离，审计 `actor` 默认写入 `current_user.email`。
- **前端层**：
  - `frontend/src/stores/auth.ts`：维护 `user`、`initialized`、`loading` 状态及 `fetchMe` / `requestCode` / `verifyLogin` / `logout`。
  - `frontend/src/pages/LoginPage.vue`：邮箱 + 验证码登录页（60s 重发倒计时）。
  - `frontend/src/pages/AdminUsersPage.vue`：管理员用户与邀请管理页。
  - `frontend/src/router/index.ts`：全局前置守卫，区分公开路由（`/login`）、登录路由与管理员路由（`meta.requiresAdmin`）。
  - `frontend/src/api/client.ts`：请求默认携带 `credentials: 'include'`，遇 `401` 触发统一未授权回调清理状态并跳转 `/login`。

## 2. 数据库 Schema 与迁移设计

### 2.1 新增表结构

1. **`users`**
   - `id`: `String(36)` PK (UUID)
   - `email`: `String(255)` NOT NULL UNIQUE（写入前统一 `strip().lower()`）
   - `display_name`: `String(128)` NULL
   - `role`: `String(16)` NOT NULL（`ADMIN` / `USER`）
   - `status`: `String(16)` NOT NULL（`INVITED` / `ACTIVE` / `DISABLED`）
   - `is_default_admin`: `Boolean` NOT NULL DEFAULT `False`（保护默认管理员不可被禁用或撤销）
   - `invited_by_user_id`: `String(36)` FK -> `users.id` (`ondelete="SET NULL"`) NULL
   - `last_login_at`: `DateTime(timezone=True)` NULL
   - `created_at`: `DateTime(timezone=True)` NOT NULL
   - `updated_at`: `DateTime(timezone=True)` NOT NULL

2. **`email_verification_codes`**
   - `id`: `String(36)` PK (UUID)
   - `email`: `String(255)` NOT NULL（索引 `ix_email_codes_email_created`）
   - `code_hash`: `String(128)` NOT NULL（HMAC-SHA256，使用 `AUTH_SECRET_KEY` + 规范化邮箱 + 6 位验证码，避免明文落库）
   - `purpose`: `String(32)` NOT NULL DEFAULT `'LOGIN'`
   - `attempts`: `Integer` NOT NULL DEFAULT `0`
   - `max_attempts`: `Integer` NOT NULL DEFAULT `5`
   - `expires_at`: `DateTime(timezone=True)` NOT NULL
   - `consumed_at`: `DateTime(timezone=True)` NULL
   - `created_at`: `DateTime(timezone=True)` NOT NULL

3. **`user_sessions`**
   - `id`: `String(36)` PK (UUID)
   - `user_id`: `String(36)` FK -> `users.id` (`ondelete="CASCADE"`) NOT NULL（索引 `ix_user_sessions_user_id`）
   - `token_hash`: `String(64)` NOT NULL UNIQUE（SHA-256 哈希；明文 32 字节 `secrets.token_urlsafe(32)` 仅存入 Cookie）
   - `expires_at`: `DateTime(timezone=True)` NOT NULL
   - `revoked_at`: `DateTime(timezone=True)` NULL
   - `last_seen_at`: `DateTime(timezone=True)` NOT NULL
   - `user_agent`: `String(255)` NULL
   - `ip_address`: `String(64)` NULL
   - `created_at`: `DateTime(timezone=True)` NOT NULL

### 2.2 现有表结构变更与存量数据迁移

1. **`accounts` 表**：
   - 新增可空列 `user_id: String(36)`（FK -> `users.id`, `ondelete="CASCADE"`）及索引 `ix_accounts_user_id`。
   - 迁移与启动回填阶段：确保默认管理员存在后，将所有 `accounts.user_id IS NULL` 的记录更新为默认管理员的 `id`。
   - 保持迁移期间允许临时 NULL -> 回填完成 -> 应用层创建账户强制写入 `user_id`（SQLite/MySQL 均无损兼容）。

2. **`profit_settings` 表**：
   - 原主键为 `scope_key`（如 `GLOBAL` / `SECURITY:{security_id}`），且存在全局唯一约束 `uq_profit_settings_security(security_id)`。
   - 多用户改造后：
     - 新增列 `user_id: String(36)`（FK -> `users.id`, `ondelete="CASCADE"`）。
     - 删除旧的 `uq_profit_settings_security(security_id)`，替换为 `uq_profit_settings_user_security(user_id, security_id)`。
     - `scope_key` 扩展长度至 `String(96)`，格式升级为：
       - 用户全局：`USER:{user_id}:GLOBAL`
       - 用户个股：`USER:{user_id}:SECURITY:{security_id}`
     - 迁移/启动回填阶段：把存量 `scope_key='GLOBAL'` 或 `SECURITY:{id}` 记录的 `user_id` 更新为默认管理员 `id`，并将 `scope_key` 原地迁移为带 `USER:{admin_id}:` 前缀的新键，保留所有原有 `target_return_pct`、`calculation_basis`、`alert_enabled`、`created_at`。

3. **旧版无 `alembic_version` 的本地 SQLite 兼容策略（关键）**：
   - 现状 `prepare_database_schema()` 在 `not has_version_table and has_known_schema` 时直接 `create_all` 并 `stamp("head")`，会导致新增加的列（如 `accounts.user_id`）无法被添加到已有表中。
   - 修复方案：在 `prepare_database_schema()` 中，若 `not has_version_table and has_known_schema`：
     - 若检测到旧库缺少 `users` 表但已有 `accounts` 表，则根据实际表存在情况先补齐历史缺失表并 `stamp("0003_position_refresh_states")`，再正常执行 `_upgrade_to_head(database_engine)`，从而通过正式 Alembic `0004` 迁移安全添加列并回填数据，绝不删库、绝不清空 `ledger.db`。

## 3. 配置与环境变量设计

在 `backend/app/config.py` 新增配置项（同步更新 `compose.yaml`）：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DEFAULT_ADMIN_EMAIL` | `""`（`console` 模式回退 `admin@localhost`，`smtp` 模式必填） | 默认管理员邮箱，启动时自动创建/同步并接管存量数据 |
| `AUTH_SECRET_KEY` | 本地开发提供固定提醒/自动生成兜底；`smtp` 生产模式建议显式配置 | 用于验证码 HMAC 哈希 |
| `AUTH_SESSION_DAYS` | `30` | 登录态 Cookie 与服务端会话有效天数（正整数） |
| `AUTH_COOKIE_NAME` | `wing_session` | 会话 Cookie 名称 |
| `AUTH_COOKIE_SECURE` | `false` | HTTPS 部署时设为 `true` |
| `EMAIL_PROVIDER` | `console` | 支持 `console` / `smtp` |
| `SMTP_HOST` | `""` | `smtp` 模式必填 |
| `SMTP_PORT` | `587` | `smtp` 模式端口 |
| `SMTP_USER` | `""` | SMTP 登录用户名（可选） |
| `SMTP_PASSWORD` | `""` | SMTP 密码（可选） |
| `SMTP_FROM` | `""` | 发件人地址（`smtp` 模式必填，未填时可回退 `SMTP_USER`） |
| `SMTP_USE_TLS` | `true` | STARTTLS 开关 |
| `SMTP_USE_SSL` | `false` | SSL 直连开关（与 `SMTP_USE_TLS` 互斥） |

> **默认管理员邮箱变更兼容**：若数据库已有 `is_default_admin=True` 的管理员记录且其邮箱为 `admin@localhost`，后续用户显式配置了真实的 `DEFAULT_ADMIN_EMAIL` 且该邮箱未被其他用户占用时，启动时自动将该默认管理员记录的邮箱更新为新配置的 `DEFAULT_ADMIN_EMAIL`（保持 `id` 不变，存量数据归属无缝延续）。

## 4. API 契约与错误码设计

### 4.1 认证领域错误码映射（`backend/app/api/errors.py` + `domain/exceptions.py`）

- `401 Unauthorized`：`UNAUTHORIZED`（未登录、会话过期或已吊销）
- `403 Forbidden`：`FORBIDDEN`（普通用户访问管理员接口、未受邀邮箱登录、账号已禁用）
- `409 Conflict`：`USER_ALREADY_EXISTS`（重复邀请已存在邮箱）
- `422 Unprocessable Entity`：`INVALID_EMAIL`、`INVALID_VERIFICATION_CODE`、`VERIFICATION_CODE_EXPIRED`、`VERIFICATION_CODE_ATTEMPTS_EXCEEDED`
- `429 Too Many Requests`：`VERIFICATION_CODE_COOLDOWN`（60 秒重发冷却期内）

### 4.2 认证接口（`/api/v1/auth`）

- `POST /api/v1/auth/request-code`
  - 请求：`{ "email": "user@example.com" }`
  - 行为：校验邮箱格式 -> 查询 `users` -> 不存在返回 `403`（未获邀请），`DISABLED` 返回 `403`（已禁用） -> 检查 60s 冷却（未过返回 `429`，`details.retryAfterSeconds`） -> 生成 6 位数字验证码、作废同邮箱之前未消费验证码、写入哈希记录 -> 调用 `EmailSender` 发送。
  - 响应：`200` `{ "sent": true, "expiresInSeconds": 300, "cooldownSeconds": 60 }`
- `POST /api/v1/auth/verify-code`
  - 请求：`{ "email": "user@example.com", "code": "123456" }`
  - 行为：校验用户存在且非 `DISABLED` -> 取最新未消费验证码 -> 校验未过期、未超 5 次错误 -> 常量时间比对 HMAC 哈希 -> 错误则 `attempts += 1` 返回 `422`；成功则标记 `consumed_at`；若原状态为 `INVITED` 则置为 `ACTIVE` 并创建默认 CNY 账户与 `DEFAULT` 策略；更新 `last_login_at`；签发 30 天服务端会话并设置 `HttpOnly; SameSite=Lax; Path=/` Cookie。
  - 响应：`200` `{ "user": UserRead }`
- `GET /api/v1/auth/me`
  - 需有效登录态（未登录返回 `401`）。
  - 响应：`200` `{ "user": UserRead }`
- `POST /api/v1/auth/logout`
  - 吊销当前 Cookie 对应的服务端会话（幂等），并清除 Cookie。
  - 响应：`204 No Content`

### 4.3 管理员接口（`/api/v1/admin`，需 `require_admin`）

- `GET /api/v1/admin/users`
  - 返回全部用户与邀请列表（按创建时间倒序），包含 `id`, `email`, `displayName`, `role`, `status`, `isDefaultAdmin`, `lastLoginAt`, `createdAt`。
- `POST /api/v1/admin/users/invite`
  - 请求：`{ "email": "friend@example.com", "displayName": "张三" }`
  - 行为：规范化并校验邮箱 -> 若已存在返回 `409` -> 创建 `role="USER"`, `status="INVITED"`, `invited_by_user_id=admin.id` -> 调用 `EmailSender.send_invite()` 发送邀请提示邮件（邮件发送失败记录警告日志并返回 `inviteEmailSent: false`，不回滚邀请记录，方便管理员线下通知）。
  - 响应：`201` `{ "user": UserRead, "inviteEmailSent": true }`
- `DELETE /api/v1/admin/users/{user_id}/invite`
  - 仅允许撤销 `status == "INVITED"` 且非默认管理员的记录；已激活用户返回 `409`。
  - 响应：`204 No Content`
- `PATCH /api/v1/admin/users/{user_id}/status`
  - 请求：`{ "status": "DISABLED" | "ACTIVE" }`
  - 行为：禁止操作自己或默认管理员（返回 `422`）；切换为 `DISABLED` 时，立即将该用户全部未吊销 `user_sessions.revoked_at` 设为当前时间；不允许对 `INVITED` 用户直接启用（需经邮箱验证码首登激活）。
  - 响应：`200` `{ "user": UserRead }`

### 4.4 业务接口用户隔离改造清单

所有 `/api/v1/*` 业务接口注入 `current_user: User = Depends(get_current_user)`：

- `GET /accounts`：只查询 `Account.user_id == current_user.id`（若当前用户尚无账户则按需保证默认账户或返回列表）。
- `POST /accounts`：写入 `user_id=current_user.id`。
- `resolve_account(session, user_id, account_id)`：
  - 若传 `account_id`：必须 `Account.id == account_id` 且 `Account.user_id == user_id`，否则抛 `EntityNotFoundError`（`404`）。
  - 若未传 `account_id`：调用 `ensure_default_context(session, user_id)`，获取或创建当前用户的默认 CNY 账户与 `DEFAULT` 策略。
- `GET /trades`：未传 `accountId` 时也必须按 `Trade.account_id IN (当前用户所有账户ID)` 过滤！
- `PATCH /trades/{trade_id}`、`POST /trades/{trade_id}/void`：先校验 `trade.account.user_id == current_user.id`，否则 `404`。
- `GET /lots/{lot_id}/matches`、`GET/POST /lots/{lot_id}/targets`：先校验 `lot.account.user_id == current_user.id`，否则 `404`。
- `PATCH/DELETE /targets/{target_id}`：先校验 `target.lot.account.user_id == current_user.id`，否则 `404`。
- `GET/PUT /profit-settings` 与 `/securities/{security_id}/profit-settings`：使用 `ProfitSettingsService.scope_key(user_id=current_user.id, security_id=...)`；`effective_target(session, lot)` 从 `lot.account.user_id` 解析所属用户的设置。
- `GET /quotes`、`GET /securities`、`POST /securities`、`GET /securities/lookup`、`GET /securities/{id}/intraday`：要求 `get_current_user` 登录态，数据共享；其中 `GET /quotes` 未传 `symbols` 时仅扫描当前用户的持仓/自选证券范围。
- 写操作 `actor`：优先使用 `current_user.email`（保留 `X-Actor` 仅在显式需要时辅助，或直接以登录用户邮箱为准确保审计不可伪造）。

## 5. 前端设计

1. **`frontend/src/stores/auth.ts`**：
   - 状态：`user: AuthUser | null`、`initialized: boolean`、`loading: boolean`。
   - 方法：`init()`（调用 `GET /auth/me`，401 视为未登录）、`requestCode(email)`、`verifyCode(email, code)`、`logout()`、`clearAuth()`。
2. **`frontend/src/api/client.ts`**：
   - `fetch` 增加 `credentials: 'include'`。
   - 新增认证与管理员 API 方法。
   - 注册未授权钩子：当非 `/auth/*` 请求返回 `401` 时，通知 `authStore.clearAuth()` 并跳转 `/login`。
3. **`frontend/src/router/index.ts`**：
   - 新增路由：
     - `/login`（`meta: { public: true, title: '登录' }`）
     - `/admin/users`（`meta: { requiresAdmin: true, title: '用户管理' }`）
   - `router.beforeEach`：首次导航先 `await authStore.init()`；未登录访问非 public 路由跳转 `/login?redirect=...`；已登录访问 `/login` 跳转 `/dashboard`；非管理员访问 `requiresAdmin` 路由跳转 `/dashboard`。
4. **`frontend/src/App.vue`**：
   - 在 `/login` 路由下隐藏侧边栏与顶栏，全屏居中渲染登录卡片。
   - 登录后在侧边栏展示“用户管理”菜单项（仅 `user.role === 'ADMIN'` 可见）；顶栏右侧展示当前用户邮箱、角色标签（管理员/普通用户）与“退出登录”按钮。
5. **新页面**：
   - `frontend/src/pages/LoginPage.vue`：简洁卡片式表单，邮箱输入 + 获取验证码（60 秒倒计时防连点）+ 6 位验证码输入 + 登录按钮 + 仅限受邀用户提示。
   - `frontend/src/pages/AdminUsersPage.vue`：顶部邀请表单（邮箱 + 可选显示名 + 发送邀请按钮）+ 用户列表表格（邮箱、显示名、角色、状态标签、最近登录、操作列：撤销邀请 / 禁用 / 启用）。

## 6. 兼容性、安全与回滚考量

- **测试夹具兼容**：现有集成测试（`test_api_v1.py`、`test_dashboard_api.py` 等）直接调用 `/api/v1/*` 且未登录。在 `backend/tests/conftest.py` 提供默认已认证测试客户端夹具（以默认管理员身份登录，并保留未认证客户端夹具用于测试 401/登录流），避免破坏已有测试覆盖并减少重复样板。
- **Playwright E2E 兼容**：`frontend/e2e/*.spec.ts` 在测试前需通过认证辅助步骤（如使用 `console` 模式或测试辅助完成登录）进入应用，并新增登录、邀请与隔离 E2E/集成验证。
- **防刷与隐私**：
  - 验证码使用 `secrets.randbelow(1_000_000)` 补零 6 位，HMAC-SHA256 存储，常量时间 `hmac.compare_digest` 校验。
  - 会话 token 使用 `secrets.token_urlsafe(32)`，数据库仅存 SHA-256 哈希。
  - 跨用户访问资源统一返回 `404` 而非 `403`，避免暴露他人资源 ID 存在性。
- **回滚考量**：迁移 `0004` 提供对称 `downgrade()`；在本地环境执行任何验证前绝不删除或清空 `ledger.db`，测试全部使用独立临时库（`tmp_path`）。
