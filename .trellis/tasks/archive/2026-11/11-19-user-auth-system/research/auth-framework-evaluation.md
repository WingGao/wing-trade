# 调研：FastAPI 用户鉴权框架选型评估

## 背景与约束

- **当前后端技术栈**：FastAPI + **同步** SQLAlchemy 2.0（`Session`）+ Alembic + SQLite / MySQL（`pymysql`）。
- **业务需求特征**：无密码（仅邮箱 + 6 位验证码登录）、不开放注册、管理员按邮箱定向邀请、服务端 30 天 Session（HttpOnly Cookie）、禁用用户立即踢下线、业务表按 `user_id` / `account_id` 严格隔离。

## 候选方案对比

| 方案 | 成熟度 | 与当前架构匹配度 | 优点 | 主要问题 / 成本 |
|---|---|---|---|---|
| **1. FastAPI 原生 `Depends` + `APIKeyCookie` + 标准库密码学原语**（当前 `design.md` 方案） | 极高（FastAPI 官方推荐模式） | **完全匹配**（同步 SQLAlchemy、零额外基础设施） | 零额外重型依赖；天然契合无密码 OTP + 邀请制 + 服务端 Session；逻辑透明、易审计、易单测；禁用用户即时失效 | 需要自行编写约 200~300 行 `AuthService` 与 `auth/admin` 路由 |
| **2. `fastapi-users`** | 高（FastAPI 社区最知名用户库） | **较低** | 内置用户表抽象、Cookie/JWT 传输策略、FastAPI 依赖注入 | 1) 强依赖 **异步 SQLAlchemy（`AsyncSession`）**，本项目全栈是同步 `Session`，引入会导致双连接池/双驱动（需加 `aiosqlite` / `aiomysql`）<br>2) 核心面向“密码登录 + 开放注册 + 找回密码”，**不原生支持无密码 Email OTP 与邀请制**，反而要魔改并关闭其大部分默认路由 |
| **3. 独立身份服务（Authentik / Authelia / Keycloak / Logto / SuperTokens）** | 极高（企业级 IdP） | **过重** | 开箱即用 SSO、MFA、完善管理后台 | 需在 `compose.yaml` 新增独立容器/数据库/反代配置，大幅抬高自托管运维门槛；业务后端仍需维护本地用户映射与账本数据隔离 |
| **4. `Authlib` / `PyJWT` / `itsdangerous`** | 极高 | 部分适用 | 成熟的标准协议/签名库 | 本项目已选**服务端 DB Session（随机 token 哈希）**且本期不做 OAuth，不需要 JWT 或第三方登录协议栈；若未来扩展 OAuth 登录可引入 `Authlib` |

## 结论与建议

1. **推荐继续使用方案 1（FastAPI 原生鉴权原语 + Python 标准库 `secrets` / `hmac` / `hashlib`）**：
   - 鉴权里真正高风险、不该自己造轮子的部分主要是**密码哈希算法**（bcrypt/argon2）、**JWT/OAuth 协议实现**和**随机数生成**。
   - 本次设计采用**无密码 + 服务端随机 Session**，避开了密码存储和 JWT 协议复杂度，密码学原语全部直接复用 Python 标准库（`secrets.token_urlsafe`、`secrets.randbelow`、`hmac.compare_digest`、`hashlib.sha256`），HTTP 鉴权使用 FastAPI 原生 `APIKeyCookie` + `Depends`，既安全又与现有同步 SQLAlchemy 架构无缝一致。
2. **可选演进路径**：若未来希望支持 GitHub/Google 登录或企业 SSO，可在现有 `users` + `user_sessions` 基础上平滑叠加 `Authlib`（OAuth2/OIDC），无需推翻现有数据隔离设计。
