# 验证记录

## 已通过

- `uv run --project backend python -m pytest backend/tests -q`：115 passed。
- `uv run --project backend python -m pytest backend/tests/integration/test_database_startup.py backend/tests/unit/test_config.py -q`：9 passed。
- 本次改动文件 Ruff：All checks passed。
- `uv run --project backend mypy backend/app`：55 个源文件无问题。
- `docker compose --env-file .env.example config --quiet`：通过。
- 启用 `.env.example` 时 Compose 服务为 `mysql`、`backend`、`frontend`。
- 清空 `COMPOSE_PROFILES` 后 Compose 服务为 `backend`、`frontend`，不启动 MySQL。
- MySQL 离线 Alembic DDL：三个 revision 均使用 MySQLImpl 正确生成。
- `git diff --check`：通过。
- `.env` 已由 `.gitignore` 忽略。

## 未完成

- 真实 MySQL 容器冒烟未完成：Docker 引擎可用，但 `mysql:8.4` 镜像层持续数分钟无下载进度，已停止拉取。
- 因此尚未在真实 MySQL 上执行 `alembic upgrade head`、启动应用和请求 `/ready`。

## 约束确认

- 未读取或输出真实 `.env`。
- 未删除、清空或改写 `ledger.db` 及 `ledger - 副本.db`。
- SQLite 启动迁移测试验证了旧库接管后业务行保留且 `PRAGMA integrity_check=ok`。
