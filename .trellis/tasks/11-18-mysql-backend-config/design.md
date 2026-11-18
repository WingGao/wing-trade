# MySQL 后端配置设计

## 目标

让运行环境只通过 `.env` 切换数据库，同时保留 `DATABASE_URL` 和 SQLite 的兼容性。

## 配置解析

- `DATABASE_URL` 存在时直接使用，优先级最高。
- 否则读取 `DATABASE_BACKEND`，支持 `sqlite` 和 `mysql`，默认 `sqlite`。
- MySQL 模式使用 SQLAlchemy `URL.create` 组合 `MYSQL_*` 字段，并附加 `charset=utf8mb4`，避免密码特殊字符导致 URL 解析错误。
- Compose 在容器内把 MySQL 主机覆盖为服务名 `mysql`；本地直接运行时使用 `.env` 中的 `MYSQL_HOST`。

## 连接策略

- 所有数据库启用 `pool_pre_ping=True`。
- MySQL 使用 `pool_recycle=1800`，降低服务端回收空闲连接造成的偶发断连。
- 继续只在 SQLite 上安装 `foreign_keys` 和 WAL PRAGMA。

## Compose

- 增加 MySQL 8.4 服务和健康检查。
- 使用命名卷 `mysql_data` 持久化。
- 后端复用 `.env` 中的 MySQL 库名、用户和密码，主机固定为 Compose 服务名。
- 保留 `./data` 挂载，确保 SQLite 模式仍可使用原数据库路径。

## 验证

- 单元测试验证配置优先级、URL 编码和非法配置。
- 配置 `docker compose config` 验证变量展开。
- 若 Docker 可用，启动 MySQL，执行 Alembic 迁移并检查 `/ready`。
- 不接触本地 `ledger.db`。
