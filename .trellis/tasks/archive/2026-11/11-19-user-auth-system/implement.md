# 实施计划：用户体系与邮箱验证码登录

## 执行步骤清单

### 阶段 1：配置、数据模型与无损迁移

- [x] 1. 扩展 `backend/app/config.py`，新增 `DEFAULT_ADMIN_EMAIL`、`AUTH_SECRET_KEY`、`AUTH_SESSION_DAYS`（默认 30）、`AUTH_COOKIE_NAME`、`AUTH_COOKIE_SECURE`、`EMAIL_PROVIDER`（`console|smtp`）及 `SMTP_*` 配置项与启动校验；补充单元测试。
- [x] 2. 在 `backend/app/domain/enums.py` 与 `backend/app/domain/exceptions.py` 新增角色/状态枚举（`UserRole`、`UserStatus`）及认证授权异常类；在 `backend/app/api/errors.py` 补齐 `401`、`403`、`409`、`422`、`429` 状态码映射。
- [x] 3. 新增 `backend/app/models/auth.py`（`User`、`EmailVerificationCode`、`UserSession`），更新 `backend/app/models/ledger.py`（`Account.user_id`、`ProfitSetting.user_id` 与新唯一约束）及 `backend/app/models/__init__.py`。
- [x] 4. 编写 Alembic 迁移 `backend/alembic/versions/0004_user_auth_system.py`（兼容 SQLite 与 MySQL），并修复 `backend/app/db/migrations.py` 中旧版无 `alembic_version` SQLite 库的无损升级路径。
- [x] 5. 实现 `backend/app/services/auth_bootstrap.py`（`ensure_default_admin_and_backfill`），在 `backend/app/main.py` 启动阶段执行，确保默认管理员存在并无损回填存量 `accounts` 与 `profit_settings`。

### 阶段 2：邮件发送与认证/用户管理服务

- [x] 6. 实现 `backend/app/services/email.py`（`EmailSender` 抽象、`ConsoleEmailSender`、`SmtpEmailSender` 及工厂方法），所有方法附中文注释。
- [x] 7. 实现 `backend/app/services/auth.py`（`AuthService`）：邮箱规范化、6 位验证码生成/HMAC 哈希/60s 冷却/5 分钟过期/5 次错误失效、首登自动激活并创建默认账户与策略、30 天服务端会话签发/校验/登出吊销、管理员邀请/撤销/禁用启用（禁用即刻吊销全部会话）。
- [x] 8. 更新 `backend/app/services/profit_settings.py`，使 `ProfitSettingsService.scope_key()` 与 `effective_target()` 按 `user_id` 隔离。

### 阶段 3：API 鉴权、用户管理接口与全量隔离

- [x] 9. 重构 `backend/app/api/deps.py`：新增 `get_current_user`、`require_admin`，更新 `ensure_default_context`、`resolve_account` 及资源归属校验辅助函数（`trade` / `lot` / `target`）。
- [x] 10. 新增 `backend/app/api/schemas/auth.py`、`backend/app/api/v1/auth.py`、`backend/app/api/v1/admin.py`，并挂载至 `backend/app/api/v1/router.py`。
- [x] 11. 改造 `backend/app/api/v1/router.py` 与 `backend/app/api/v1/market.py` 中所有业务接口：接入 `get_current_user`，强制按当前用户隔离账户、流水、持仓、止盈设置、自选股与行情默认范围，审计 `actor` 默认使用当前登录用户邮箱。
- [x] 12. 更新 `compose.yaml`（添加认证与邮件相关环境变量透传与默认值）。

### 阶段 4：后端测试与回归验证

- [x] 13. 新增 `backend/tests/auth_helpers.py` 并更新既有 API 集成测试，保证全部既有测试通过。
- [x] 14. 新增 `backend/tests/unit/test_auth_config_and_service.py` 与 `backend/tests/integration/test_auth_and_isolation.py`，覆盖配置校验、验证码安全规则、未登录 401、非受邀 403、管理员邀请与首登激活、30 天会话与禁用即时踢下线、跨用户严格隔离、旧版 SQLite 无损迁移。

### 阶段 5：前端认证、路由守卫与管理员页面

- [x] 15. 更新 `frontend/src/api/types.ts` 与 `frontend/src/api/client.ts`（`credentials: 'include'`、401 统一处理、新增 auth/admin 接口封装）。
- [x] 16. 新增 `frontend/src/stores/auth.ts` 与 `frontend/src/pages/LoginPage.vue`、`frontend/src/pages/AdminUsersPage.vue`。
- [x] 17. 更新 `frontend/src/router/index.ts`（全局鉴权与管理员守卫）与 `frontend/src/App.vue`（登录页无壳展示、顶栏当前用户与登出按钮、管理员侧边栏入口）。
- [x] 18. 更新 `frontend/e2e/` 既有测试的 `/auth/me` 路由模拟。

## 验证结果

- `uv run mypy app`：通过（62 个源文件，0 问题）
- `uv run ruff check`（所有本次新增/修改的后端文件）：通过（All checks passed）
- `pytest -q`：通过（120 passed，0 failed）
- `pnpm run type-check && pnpm run build`：通过（0 错误）
