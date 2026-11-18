# 实施计划

1. 扩展 `backend/app/config.py`，支持结构化 MySQL 配置和显式 URL 优先级。
2. 更新 `backend/app/db/session.py`，增加连接探活与 MySQL 连接回收。
3. 增加 PyMySQL 依赖并更新 `backend/uv.lock`。
4. 更新 `compose.yaml`，加入 MySQL 服务、健康检查、持久化卷和后端连接变量。
5. 增加 `.env.example` 和 README 中文配置说明。
6. 运行配置测试、后端测试、Compose 配置检查及 MySQL 冒烟验证。
