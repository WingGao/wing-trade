# 技术设计文档（Design）

## 1. 架构与边界

本次改造集中在后端配置模块 [backend/app/config.py](file:///d:/Projs/wing-trade/backend/app/config.py)，对外保持 `Settings` 不可变 dataclass 结构及全局 `settings` 实例不变，不侵入业务服务层与 API 路由层。

```
CONFIG_FILE / 默认探测 (cwd -> repo_root)
          │
          ▼
   YAML 解析与结构校验 (yaml.safe_load + 允许键白名单)
          │
          ▼
   字段级配置解析器 (YAML > os.getenv > 默认值)
          │
          ▼
   Settings(..., __post_init__ 统一业务校验)
```

## 2. YAML 结构与字段映射契约

| YAML 路径 | 对应环境变量 | `Settings` 字段 / 用途 | 类型与特殊规则 |
|---|---|---|---|
| `database.url` | `DATABASE_URL` | `database_url`（最高优先） | 字符串；空字符串视为未显式指定 |
| `database.backend` | `DATABASE_BACKEND` | 推导 `database_url` | `sqlite` / `mysql`，默认 `sqlite` |
| `database.sqlite_path` | `DATABASE_SQLITE_PATH` | 推导 SQLite URL | 字符串；空字符串回退默认 `sqlite:///./ledger.db` |
| `database.echo_sql` | `ECHO_SQL` | `echo_sql` | 布尔值，默认 `False` |
| `database.mysql.host` | `MYSQL_HOST` | MySQL 连接串 | `backend=mysql` 时必填 |
| `database.mysql.port` | `MYSQL_PORT` | MySQL 连接串 | 整数 `1..65535`，`backend=mysql` 时必填 |
| `database.mysql.database` | `MYSQL_DATABASE` | MySQL 连接串 | `backend=mysql` 时必填 |
| `database.mysql.user` | `MYSQL_USER` | MySQL 连接串 | `backend=mysql` 时必填 |
| `database.mysql.password` | `MYSQL_PASSWORD` | MySQL 连接串 | `backend=mysql` 时必填，**不执行 `strip()`** |
| `logging.level` | `LOG_LEVEL` | `log_level` | 字符串，默认 `INFO` |
| `quote.provider` | `QUOTE_PROVIDER` | `quote_provider` | `eastmoney` / `mock`，默认 `eastmoney` |
| `quote.timeout_seconds` | `QUOTE_TIMEOUT_SECONDS` | `quote_timeout_seconds` | 正有限数（转 `Decimal`），默认 `5` |
| `auth.default_admin_email` | `DEFAULT_ADMIN_EMAIL` | `default_admin_email` | 邮箱；`console` 模式默认 `admin@localhost` |
| `auth.secret_key` | `AUTH_SECRET_KEY` | `auth_secret_key` | 字符串，默认开发密钥 |
| `auth.session_days` | `AUTH_SESSION_DAYS` | `auth_session_days` | 正整数，默认 `30` |
| `auth.cookie_name` | `AUTH_COOKIE_NAME` | `auth_cookie_name` | 非空字符串，默认 `wing_session` |
| `auth.cookie_secure` | `AUTH_COOKIE_SECURE` | `auth_cookie_secure` | 布尔值，默认 `False` |
| `email.provider` | `EMAIL_PROVIDER` | `email_provider` | `console` / `smtp`，默认 `console` |
| `email.smtp.host` | `SMTP_HOST` | `smtp_host` | `smtp` 模式必填 |
| `email.smtp.port` | `SMTP_PORT` | `smtp_port` | 整数 `1..65535`，默认 `587` |
| `email.smtp.user` | `SMTP_USER` | `smtp_user` | 字符串，默认 `""` |
| `email.smtp.password` | `SMTP_PASSWORD` | `smtp_password` | 字符串，默认 `""`，**不执行 `strip()`** |
| `email.smtp.from` | `SMTP_FROM` | `smtp_from` | 缺省回退 `smtp_user` |
| `email.smtp.use_tls` | `SMTP_USE_TLS` | `smtp_use_tls` | 布尔值，与 `use_ssl` 联合推导 |
| `email.smtp.use_ssl` | `SMTP_USE_SSL` | `smtp_use_ssl` | 布尔值，与 `use_tls` 联合推导 |
| `frontend.dist_dir` | `FRONTEND_DIST_DIR` | `frontend_dist_dir` | 字符串，默认 `""` |

## 3. 配置文件发现与校验细节

### 3.1 路径探测顺序
1. 若调用 `Settings.load(config_file=...)` 传入了非空路径，或设置了非空环境变量 `CONFIG_FILE`：
   - 直接读取该路径；若文件不存在或不是常规文件，抛出 `ValueError(f"CONFIG_FILE not found: {path}")`。
2. 若未显式指定，依次检查候选路径（去重）：
   - `Path.cwd() / "config.yaml"`
   - `Path.cwd() / "config.yml"`
   - `<repo_root> / "config.yaml"`（由 `Path(__file__).resolve().parents[2]` 计算，兼容在 `backend/` 目录下启动 uvicorn 或 pytest）
   - `<repo_root> / "config.yml"`
   - 命中第一个存在的文件即停止；均不存在则使用空配置 `{}`。

### 3.2 结构校验与类型解析
- 使用 `yaml.safe_load` 解析文件，捕获 `yaml.YAMLError` 并转为带路径上下文的 `ValueError`。
- 根节点为 `None` 时转为空字典 `{}`；根节点非 `dict` 时抛出 `ValueError`。
- 维护嵌套白名单 schema（顶层 section 与各 section 允许键，以及 `database.mysql`、`email.smtp` 子对象允许键）：
  - 任何未知键立即抛出 `ValueError`（包含完整键路径，如 `Unknown config key: quote.timeout`）。
  - 分组节点值若既非 `None` 也非 `dict`，抛出 `ValueError`（如 `Config section 'database' must be a mapping`）。
- 标量解析辅助函数：
  - `_get_raw_value(yaml_data, path, env_name)`：优先取 YAML 路径上的非 `None` 值；否则取 `os.getenv(env_name)`。
  - `_parse_bool`：原生 `bool` 直接返回；字符串兼容 `1/true/yes/on` 与 `0/false/no/off`；拒绝非法非空文本或非标量类型（防止 YAML 误写字符串 `"maybe"` 静默变 `False`）。
  - `_parse_int` / `_parse_port`：注意在 Python 中 `isinstance(True, int)` 为 `True`，需显式拒绝布尔值作为端口或整数！
  - `smtp_use_tls` / `smtp_use_ssl` 联合推导：分别先按 `YAML > env` 判断是否显式提供（`None` 或 env 空串均视为未提供），再复用既有的 4 分支推导逻辑（465 默认 SSL，其余默认 STARTTLS）。

### 3.3 方法签名与测试隔离
- 新增 `@classmethod def load(cls, config_file: str | os.PathLike[str] | None = None, *, discover_default: bool = True) -> Settings`：
  - 模块级全局实例改为 `settings = Settings.load()`。
  - 保留 `Settings.from_env()` 作为兼容入口，内部直接委托 `cls.load()`，使现有 `Settings.from_env()` 调用自然支持 `CONFIG_FILE` 与 YAML。
- 测试隔离：
  - 在 `backend/tests/conftest.py`（若不存在则新增）中设置默认测试隔离策略（例如通过 `monkeypatch` 将默认配置文件发现关闭或指向临时空目录，或将默认候选路径列表暴露为模块常量/函数便于隔离），确保开发者在本地创建 `config.yaml` 后，所有既有仅测 env 的单元测试依然稳定通过；新增的 YAML 专项测试则显式写入 `tmp_path / "config.yaml"` 并通过 `CONFIG_FILE` 或 `monkeypatch.chdir(tmp_path)` 测试自动发现与显式指定。

## 4. Docker Compose 与文档配套

- `compose.yaml`：
  - 在 `services.app.environment` 中新增 `CONFIG_FILE: ${CONFIG_FILE:-}`。
  - **不在 `compose.yaml` 中硬编码 `- ./config.yaml:/app/config.yaml:ro`**，避免宿主机未创建 `config.yaml` 时 Docker 自动建出同名目录导致启动失败。
  - 用户在 Docker Compose 中使用 YAML 配置时，可将配置文件放到已挂载的 `./data/config.yaml`，并在 `.env` 中设置 `CONFIG_FILE=/app/data/config.yaml`（或在自有 override 文件中挂载）。
- `config.example.yaml`：根目录提供完整带中文注释的脱敏模板。
- `.gitignore`：新增忽略 `/config.yaml` 与 `/config.yml`（以及 `data/config.yaml` / `data/config.yml`，避免挂载目录内的敏感配置被误提交）。
- `README.md`：新增“配置文件（YAML 与环境变量）”小节，说明优先级（YAML > env > 默认值）、快速开始步骤与 Compose 挂载方式。

## 5. 风险与回滚

- **风险 1**：开发者本地已有 `config.yaml` 时，直接运行现有 `test_config.py` 等测试可能受干扰。
  - **缓解**：在 `backend/tests/conftest.py` 中对默认自动发现做测试级隔离，并在专门的测试中显式验证默认发现逻辑。
- **风险 2**：YAML 中 `bool` 被 Python 视为 `int` 子类（如 `port: true` 解析为 `1`）。
  - **缓解**：端口与整数解析函数显式拒绝 `isinstance(raw, bool)`。
- **回滚方案**：若需回滚，仅需还原 `backend/app/config.py`、`backend/pyproject.toml`、`compose.yaml`、`.gitignore`、`README.md` 并移除 `config.example.yaml` 与新增测试，无任何数据库迁移或数据变更影响。
