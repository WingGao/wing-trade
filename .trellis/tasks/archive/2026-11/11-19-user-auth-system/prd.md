# PRD：用户体系与邮箱验证码登录

## 目标与用户价值

将单人自托管的 A 股交易账本升级为受控多用户系统：

- **无密码邮箱验证码登录**：通过邮箱 + 6 位数字验证码登录，避免弱密码与密码托管风险。
- **双角色与邀请制准入**：区分管理员（`ADMIN`）与普通用户（`USER`）；不开放公开注册，仅管理员可按邮箱定向邀请新用户，保障自托管实例私密可控。
- **严格数据隔离**：每个用户仅能访问和操作归属于自己的账本数据（账户、策略、交易、批次、止盈设置、自选股等）；管理员也不越权查看普通用户账本数据。
- **存量数据无损迁移**：升级后，本地 `ledger.db` 或 MySQL 中所有存量账本数据自动归属到默认管理员名下，严禁删除或清空已有数据。

## 背景与已确认事实

基于代码库检查（`backend/app/models/ledger.py`、`backend/app/api/deps.py`、`backend/app/api/v1/router.py`、`backend/app/config.py`、`backend/app/db/migrations.py`、`backend/app/services/profit_settings.py`、`frontend/src/router/index.ts`、`frontend/src/api/client.ts`、`frontend/nginx.conf`）：

1. **数据库与迁移**：后端同时支持 SQLite（默认 `ledger.db`）和 MySQL（`pymysql`），启动时经 `prepare_database_schema()` 升级结构。现有迁移版本到 `0003_position_refresh_states.py`，且需兼容未写入 `alembic_version` 的旧版 SQLite 库。
2. **存量账务归属关系**：
   - `Account` 是当前账务隔离顶层实体，`Strategy`、`WatchlistItem`、`PositionRefreshState`、`Cycle`、`Trade`、`Lot` 均通过 `account_id` 关联到 `Account`；`TradeMatch`、`ProfitTarget` 通过 `Trade`/`Lot` 间接关联。
   - `ProfitSetting` 当前仅以全局 `GLOBAL` 或 `SECURITY:{security_id}` 作为主键，尚未区分用户，升级时需增加用户归属隔离。
   - `Security` 和 `QuoteCache` 属于公共证券资料与行情缓存，多用户共享。
   - `AuditEvent.actor` 现有字段可改为记录真实登录用户标识（如用户邮箱/ID）。
3. **部署与反代**：生产/容器部署通过 `frontend/nginx.conf` 将 `/api/` 同源反向代理至后端 `8000` 端口，适合采用 `HttpOnly; SameSite=Lax` Cookie 维护服务端会话。

## 核心需求

### R1. 用户模型与角色体系

- 新增用户实体，包含唯一规范化邮箱（小写去空白）、显示名称（可选）、角色（`ADMIN` / `USER`）、状态（`INVITED` / `ACTIVE` / `DISABLED`）、邀请人、创建时间、最近登录时间等字段。
- 系统仅支持 `ADMIN` 与 `USER` 两种角色；不提供公开注册接口或注册入口。
- 默认管理员初始化：
  - 通过环境变量 `DEFAULT_ADMIN_EMAIL` 指定默认管理员邮箱。
  - 当 `EMAIL_PROVIDER=console`（本地开发/测试模式）且未配置 `DEFAULT_ADMIN_EMAIL` 时，回退使用 `admin@localhost`；当 `EMAIL_PROVIDER=smtp` 且未配置 `DEFAULT_ADMIN_EMAIL` 时，配置校验报错阻止启动。
  - 服务启动与迁移阶段保证默认管理员存在，并将所有尚未绑定用户的存量 `accounts` 与 `profit_settings` 无损归属到该默认管理员。

### R2. 邮箱验证码登录与服务端会话

- **发送验证码**：用户输入邮箱请求发送验证码。
  - 仅允许状态为 `ACTIVE` 或 `INVITED` 的已存在用户发送验证码；未受邀邮箱或已禁用邮箱不得完成登录，且对未受邀邮箱需返回明确但不泄露敏感内部状态的错误（受控自托管场景可提示“该邮箱未获邀请或无权限登录”，禁用用户提示“账号已被禁用”）。
  - 验证码为 6 位数字，服务端仅持久化哈希值（不存明文），有效期 **5 分钟**。
  - 同一邮箱 **60 秒**内不可重复发送（冷却限制）。
- **校验登录**：用户输入邮箱 + 验证码提交登录。
  - 单个验证码最多允许输错 **5 次**，超限后立即作废。
  - 验证成功后验证码立即标记为已使用，不可复用。
  - 若用户原状态为 `INVITED`，首次登录验证成功后自动激活为 `ACTIVE`，并自动为其创建默认 CNY 账户与 `DEFAULT` 策略。
- **服务端 Session + HttpOnly Cookie**：
  - 登录成功创建服务端会话记录（数据库仅存随机 token 的哈希值），默认有效期 **30 天**（支持环境变量覆盖）。
  - 通过 `HttpOnly; SameSite=Lax` Cookie 下发（支持通过环境变量开启 `Secure` 标志）。
  - 支持查询当前登录用户信息（`/api/v1/auth/me`）与主动退出登录（`/api/v1/auth/logout`，立即删除/吊销当前服务端会话并清除 Cookie）。

### R3. 邮件发送渠道

- 支持 `EMAIL_PROVIDER=smtp|console`（默认 `console` 以兼容本地开发与自动化测试）。
- `smtp` 模式通过环境变量配置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD`、`SMTP_FROM`、`SMTP_USE_TLS`、`SMTP_USE_SSL`，使用标准库 `smtplib` + `email.message.EmailMessage` 发送中文验证码邮件及可选邀请通知邮件。
- `console` 模式将验证码与目标邮箱输出到后端结构化日志，不依赖外部邮件服务器。

### R4. 管理员邀请与用户管理

- 仅 `ADMIN` 角色可访问用户管理接口与前端“用户管理”页面；普通用户访问返回 `403`。
- 管理员功能：
  - **查看用户与邀请列表**：展示邮箱、显示名、角色、状态（待激活/正常/已禁用）、创建时间、最近登录时间。
  - **按邮箱定向邀请**：输入邮箱（及可选显示名称），创建 `USER` 角色、`INVITED` 状态的用户记录；若邮箱已存在则拒绝重复邀请（`409`）。配置了发信能力时发送邀请提示邮件（发信失败不影响记录创建或按明确错误反馈，见 `design.md`）。
  - **撤销未激活邀请**：仅可撤销/删除状态为 `INVITED` 的用户记录。
  - **禁用 / 启用普通用户**：将用户状态切换为 `DISABLED` 或恢复为 `ACTIVE`；禁用时**立即吊销该用户全部活跃会话**使其即时下线；禁止管理员禁用自己或撤销默认管理员。

### R5. 全链路鉴权与多用户数据隔离

- `/health`、`/ready` 以及登录相关接口（发送验证码、验证码登录、查询当前态或登出）除外，其余所有 `/api/v1/*` 业务接口均要求有效登录态（未登录返回 `401`）。
- 业务数据严格按当前登录用户隔离：
  - `accounts` 仅返回当前用户拥有的账户；创建账户自动绑定当前用户；传入他人 `accountId` 一律拒绝（返回 `404` 避免越权探测）。
  - `trades`、`lots`、`cycles`、`positions`、`watchlist`、`targets`、`dashboard`、`analytics`、`recalculate` 全部基于当前用户的账户归属校验；通过 `tradeId`、`lotId`、`targetId` 访问或修改他人资源一律拒绝（返回 `404`）。
  - `profit_settings` 按 `user_id` 隔离全局与个股止盈覆盖，不同用户互不影响；`ProfitSettingsService.effective_target()` 读取批次所属账户对应用户的止盈设置。
  - `securities` 与 `quotes` / `intraday` / `lookup` 保持共享，但需登录后访问。
  - 写操作审计记录 `AuditEvent.actor` 默认使用当前登录用户邮箱（或用户 ID）。

### R6. 前端登录与用户管理界面

- 新增独立登录页 `/login`：邮箱输入 + 获取验证码（带 60 秒倒计时）+ 6 位验证码输入 + 登录提交；不展示注册入口，注明“仅限受邀邮箱登录”。
- 路由守卫：应用初始化时请求 `/api/v1/auth/me` 确认登录态；未登录访问任意业务页自动重定向到 `/login?redirect=...`；已登录访问 `/login` 自动跳转 `/dashboard`；任何 API 返回 `401` 时清理本地认证状态并跳转登录页。
- 顶部栏/侧边栏展示当前登录用户邮箱、角色标识与“退出登录”按钮；仅 `ADMIN` 可见“用户管理”导航入口与 `/admin/users` 页面。
- `/admin/users` 页面提供：按邮箱邀请表单、用户与邀请状态表格、撤销邀请按钮、禁用/启用切换按钮。

## 范围之外（Out of Scope）

- 公开自助注册、找回密码、密码登录、第三方 OAuth 登录。
- 多管理员角色升降级、细粒度 RBAC 权限矩阵或团队共享账本。
- 管理员代看或代操作普通用户的持仓、交易流水和统计数据。
- 第三方 HTTP 邮件服务商专用 SDK（如 Resend/SendGrid）；本期仅支持标准 SMTP 与本地 Console 模式。
- 邮箱地址自主换绑、用户自助注销账号。

## 验收标准

1. **未登录拦截**：未登录访问 `/api/v1/dashboard`、`/api/v1/trades`、`/api/v1/accounts` 等业务接口返回 `401`；浏览器访问 `/dashboard` 自动跳转 `/login`。
2. **存量数据无损归属默认管理员**：在包含存量账户、交易、持仓批次与止盈设置的旧 SQLite/MySQL 数据库上启动新版本，原数据条数与金额完全保留，全部自动归属 `DEFAULT_ADMIN_EMAIL` 对应的默认管理员；默认管理员通过邮箱验证码登录后可完整看到原有持仓、流水与止盈设置。
3. **非受邀邮箱拦截**：使用未在用户表中的邮箱请求发送验证码或提交登录，均被拒绝，不会自动创建账号。
4. **管理员定向邀请与首登激活**：管理员在 `/admin/users` 输入新邮箱完成邀请后，该邮箱状态为 `INVITED`；被邀请人在 `/login` 获取验证码并登录成功后，状态变为 `ACTIVE`，自动拥有独立的默认 CNY 账户与空账本。
5. **验证码安全规则**：验证码 5 分钟过期；60 秒内重复发送返回限流错误（`429`）；连续输错 5 次后该验证码失效；使用过一次的验证码不能再次登录。
6. **会话有效期与即时吊销**：登录 Cookie 有效期 30 天（`HttpOnly; SameSite=Lax`）；用户退出登录后原 Cookie 立即失效；管理员禁用普通用户后，该用户已有会话立即返回 `401`，且无法重新获取验证码登录。
7. **跨用户严格数据隔离**：普通用户与管理员之间（以及不同普通用户之间）互不可见对方的账户、交易、批次、止盈设置、自选股和统计数据；直接携带他人 `accountId` / `tradeId` / `lotId` / `targetId` 请求接口返回 `404` 且不产生副作用。
8. **权限控制**：普通用户访问 `/api/v1/admin/users*` 返回 `403`，前端侧边栏不展示用户管理入口，直接访问 `/admin/users` 重定向回 `/dashboard`。
9. **双后端与测试通过**：后端单元测试与集成测试（覆盖 SQLite 存量升级、鉴权、邀请、隔离、止盈设置隔离）及前端类型检查与构建全部通过。
