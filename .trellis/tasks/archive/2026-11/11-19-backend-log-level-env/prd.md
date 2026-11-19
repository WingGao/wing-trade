# 通过环境变量配置后端日志等级

## 目标

允许部署或本地开发时通过环境变量统一调整后端日志等级，无需修改启动参数或代码。

## 已确认事实

- 当前 `backend/app/main.py` 仅声明 `ashare_ledger.api` 命名 logger，未统一设置应用日志级别。
- 当前 `backend/app/config.py` 已集中解析环境变量并提供启动期配置校验。
- 当前 `compose.yaml` 未向后端容器透传日志级别；`backend/Dockerfile` 使用固定命令启动 Uvicorn。
- 当前请求完成日志使用 INFO，异常日志使用 ERROR，符合项目日志规范中的等级用途。

## 需求

- R1：新增环境变量 `LOG_LEVEL`，同时控制 `ashare_ledger.*` 应用日志及 Uvicorn 服务、错误和访问日志。
- R2：未配置时保持现状，默认使用 `INFO`。
- R3：等级名称不区分大小写，并支持 Python 标准日志等级：`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。
- R4：非法等级必须在启动配置阶段明确报错，不能静默回退。
- R5：Docker Compose 应将 `LOG_LEVEL` 传入后端容器。
- R6：在 `.env.example` 中提供带中文说明的配置示例。

## 验收标准

- AC1：设置 `LOG_LEVEL=debug` 后，`Settings.from_env().log_level` 为 `DEBUG`。
- AC2：未设置 `LOG_LEVEL` 时，`Settings.from_env().log_level` 为 `INFO`。
- AC3：设置非法值时启动配置抛出包含 `LOG_LEVEL` 的 `ValueError`。
- AC4：后端启动后，应用 logger 与 Uvicorn 日志按配置等级过滤；`WARNING` 下不再输出 INFO 请求完成日志和 INFO 访问日志。
- AC5：相关单元测试、后端测试和静态编译检查通过。

## 范围外

- 不新增日志文件、日志轮转、远程日志采集或结构化 JSON 输出。
- 不调整现有日志内容、字段或安全脱敏规则。
- 不修改 `ledger.db` 或数据库结构。
- 不修改其他进行中任务的功能与代码。
