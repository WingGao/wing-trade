# 支持 MySQL 数据库配置

## Goal

让后端通过 .env 切换 MySQL，补齐驱动、连接池、Compose 编排、迁移与文档，并保持 SQLite 默认行为兼容。

## Requirements

- 用户通过根目录 `.env` 配置 MySQL，不需要再修改代码或 Compose 文件。
- 支持 `DATABASE_BACKEND=sqlite|mysql`；未配置时保持 SQLite 默认行为。
- MySQL 模式通过 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD` 生成连接地址，密码不得要求手工 URL 编码。
- 显式提供 `DATABASE_URL` 时仍以其为准，保持现有部署方式兼容。
- 后端增加 PyMySQL 运行时依赖，并为 MySQL 连接启用连接前探活和连接回收。
- Docker Compose 增加 MySQL 8.4、健康检查、持久化卷，并让后端等待 MySQL 就绪。
- README 说明 `.env` 配置、启动、迁移和 SQLite 数据保留要求。
- 不删除、清空或自动改写本地 `ledger.db`。

## Acceptance Criteria

- [x] `.env` 仅配置 MySQL 主机、端口、库名、用户和密码即可启动 MySQL 后端。
- [x] 未配置 MySQL 或显式使用 SQLite 时，现有 SQLite 启动方式仍可用。
- [x] MySQL URL 使用 `mysql+pymysql`、`utf8mb4`，特殊字符密码通过结构化 URL 正确转义。
- [x] Compose 中 MySQL 数据持久化到命名卷，后端只在数据库健康后启动。
- [x] 新增配置单元测试，覆盖 MySQL URL 生成、显式 URL 优先级和非法后端拒绝。
- [ ] 后端现有测试通过，并在可用 Docker 环境验证 MySQL 迁移、应用启动和 `/ready`。

## Non-goals

- 不自动迁移现有 SQLite 账务数据到 MySQL。
- 不改变账务模型、金额精度规则或 API 行为。
- 不修改或清理任何现有本地数据库文件。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
