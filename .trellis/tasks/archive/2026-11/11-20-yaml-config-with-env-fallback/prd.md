# 支持优先读取 YAML 配置并回退环境变量（PRD）

## 1. 目标与用户价值

后端应用配置目前仅支持通过环境变量读取（[backend/app/config.py](file:///d:/Projs/wing-trade/backend/app/config.py)）。为提升本地开发与自托管部署时的可读性与可维护性，本次任务为后端新增**结构化 YAML 配置文件**支持，并提供完整的模板、忽略规则与部署配套，使配置加载遵循**字段级 `YAML > 环境变量（env）> 内置默认值`**的明确优先级，同时保持纯环境变量部署与现有测试 100% 向后兼容。

## 2. 需求清单

### R1. 配置优先级与回退规则
- 所有应用配置项均按字段粒度遵循优先级：**YAML 配置 > 环境变量（env）> 内置默认值**。
- 当 YAML 中未定义某项或其值为 `null` 时，该项自动回退到对应的环境变量；若环境变量也未提供，则回退到 `Settings` 内置默认值。
- YAML 中的有效假值（如布尔 `false`、整数 `0`）必须作为显式配置生效，不得误判为空值而回退到环境变量。
- 空字符串在可选路径/连接串字段（如 `database.url: ""`、`database.sqlite_path: ""`、`email.smtp.from: ""`、`frontend.dist_dir: ""`）中视为未显式指定，允许继续按回退/默认规则处理；密码字段（`database.mysql.password`、`email.smtp.password`）必须原样保留首尾空白与特殊字符，不得 `strip()`。

### R2. 结构化 YAML Schema 与字段映射
- YAML 配置文件采用按模块分组的结构化嵌套格式，完整覆盖现有 25 个环境变量对应的配置项：
  - `database`: `url`、`backend`、`sqlite_path`、`echo_sql`、`mysql`（`host`、`port`、`database`、`user`、`password`）
  - `logging`: `level`
  - `quote`: `provider`、`timeout_seconds`
  - `auth`: `default_admin_email`、`secret_key`、`session_days`、`cookie_name`、`cookie_secure`
  - `email`: `provider`、`smtp`（`host`、`port`、`user`、`password`、`from`、`use_tls`、`use_ssl`）
  - `frontend`: `dist_dir`
- 保留既有所有业务校验与推导规则：
  - `database.url` 非空时优先级高于 `database.backend` 与 `database.mysql.*` 组合配置；
  - `database.backend=mysql` 时校验 MySQL 必填项与端口范围（`1..65535`），并安全转义密码生成 `mysql+pymysql://...` 连接串；
  - `email.provider=smtp` 时校验 `default_admin_email`、`smtp.host`、`smtp.from`（缺省回退 `smtp.user`）；
  - `smtp.use_tls` 与 `smtp.use_ssl` 互斥，均未显式配置时按端口推导（`465` 默认 SSL，其余默认 STARTTLS）；
  - `logging.level`、`quote.provider`、`quote.timeout_seconds`、`auth.session_days`、`auth.cookie_name`、`auth.default_admin_email` 保持原有合法性校验。

### R3. 配置文件发现与错误显式暴露
- 支持通过环境变量 `CONFIG_FILE` 显式指定 YAML 配置文件路径：
  - 若显式指定但文件不存在、不可读、语法非法或根节点不是映射对象（mapping），启动阶段直接抛出 `ValueError`，不得静默忽略。
- 未设置 `CONFIG_FILE` 时，按顺序自动探测默认路径：当前工作目录下的 `config.yaml`、`config.yml`，以及项目根目录下的 `config.yaml`、`config.yml`；若均不存在，则无缝回退到纯环境变量与默认值。
- 空 YAML 文件（仅含注释或空白）视为有效空配置，正常回退到环境变量与默认值。
- 若 YAML 中包含未识别的顶层分组或未识别字段名，直接抛出 `ValueError` 提示非法配置项，防止拼写错误被静默忽略。

### R4. 依赖、向后兼容与测试隔离
- 在 [backend/pyproject.toml](file:///d:/Projs/wing-trade/backend/pyproject.toml) 中显式声明 `PyYAML` 依赖，确保本地与 Docker 构建均可正常解析 YAML。
- 保持 `Settings` 不可变 dataclass 契约与 `Settings.from_env()` 调用兼容，并新增 `Settings.load(...)` 统一入口。
- 单元测试需具备默认配置路径隔离能力，避免开发者在本地仓库根目录放置 `config.yaml` 后污染无关单元测试。

### R5. 配套模板、忽略规则与部署文档
- 在项目根目录新增脱敏模板 `config.example.yaml`，包含全部分组、中文注释与安全占位值。
- 更新 [.gitignore](file:///d:/Projs/wing-trade/.gitignore)，忽略根目录本地 `config.yaml` 与 `config.yml`，防止敏感信息误提交。
- 更新 [compose.yaml](file:///d:/Projs/wing-trade/compose.yaml)，支持传递 `CONFIG_FILE` 环境变量（默认留空，不破坏现有未提供 YAML 时的 `docker compose up --build` 行为）。
- 更新 [README.md](file:///d:/Projs/wing-trade/README.md)，补充 YAML 配置方式、优先级说明（YAML > env > 默认值）及 Docker Compose 下的使用示例（如放入 `./data/config.yaml` 配合 `CONFIG_FILE=/app/data/config.yaml`）。

## 3. 范围边界（Out of Scope）

- 不支持运行时热重载（Hot Reload）配置文件（仍为应用启动时加载不可变 `Settings`）。
- 不支持扁平键值对 YAML 格式或 JSON/TOML 等其他配置文件格式。
- 不支持在 YAML 中引入 `${ENV_VAR}` 模板插值语法（未配置项直接通过字段级回退读取环境变量即可）。
- 不修改前端代码，不修改数据库 schema，不触碰本地 `ledger.db`。

## 4. 验收标准

1. **YAML 优先覆盖 env**：同时存在 YAML 配置与环境变量时，YAML 中显式配置的字段（包括 `false`、`0` 等假值）覆盖同名环境变量。
2. **字段级回退 env**：YAML 仅配置部分分组/字段（或某字段为 `null`）时，其余未配置项正确回退到环境变量，环境变量也未提供时回退到默认值。
3. **无 YAML 向后兼容**：未提供 `CONFIG_FILE` 且默认路径无 `config.yaml`/`config.yml` 时，现有纯环境变量行为与全部历史单元测试保持通过。
4. **错误拦截**：显式 `CONFIG_FILE` 不存在、YAML 语法错误、根节点非 mapping、包含未知配置键、以及所有业务约束违规（MySQL/SMTP 必填、TLS/SSL 互斥、非法端口/超时/日志等级等）均抛出明确 `ValueError`。
5. **配套齐全**：存在 `config.example.yaml`、`.gitignore` 忽略 `config.yaml`/`config.yml`、`compose.yaml` 支持 `CONFIG_FILE`、`README.md` 已更新说明，且 `ruff`、`mypy`、`pytest` 全部通过。
