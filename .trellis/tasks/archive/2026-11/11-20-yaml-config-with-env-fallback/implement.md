# 执行计划（Implementation Plan）

## 1. 实施步骤清单

1. **添加 PyYAML 依赖**
   - 修改 [backend/pyproject.toml](file:///d:/Projs/wing-trade/backend/pyproject.toml)，在 `dependencies` 中加入 `"PyYAML>=6.0,<7.0"`。
   - 检查本地 `uv` 环境是否能正常解析并安装 `PyYAML`（以及可选类型桩或 mypy 兼容处理）。

2. **实现结构化 YAML 配置加载与 env 回退**
   - 修改 [backend/app/config.py](file:///d:/Projs/wing-trade/backend/app/config.py)：
     - 定义结构化 YAML 白名单 schema 与路径探测逻辑（`CONFIG_FILE` 优先，其次 `cwd` 与项目根目录的 `config.yaml` / `config.yml`）。
     - 实现安全的 YAML 解析、未知键校验、非 mapping 拦截与字段级 `YAML > env > 默认值` 解析器（所有函数均附中文注释）。
     - 处理 `bool` 不误判为 `int`、密码不 `strip()`、假值（`false`/`0`）不回退 env、`smtp.use_tls`/`use_ssl` 混合来源推导、`database.url` 与 `database.mysql` 优先级。
     - 新增 `Settings.load(...)`，保留 `Settings.from_env()` 兼容委托，模块级使用 `settings = Settings.load()`。

3. **完善单元测试与测试隔离**
   - 检查/新增 `backend/tests/conftest.py`，确保默认测试环境下不会意外读取开发者本地根目录的 `config.yaml`。
   - 在 [backend/tests/unit/test_config.py](file:///d:/Projs/wing-trade/backend/tests/unit/test_config.py) 中补充覆盖以下场景的单元测试：
     - 完整结构化 YAML 加载（数据库、行情、日志、认证、邮件、前端目录）；
     - 优先级验证：YAML 覆盖 env（含 `false`、`0` 等假值）；
     - 部分 YAML + `null` 字段级回退 env 与默认值；
     - `CONFIG_FILE` 显式指定路径与默认 `config.yaml` / `config.yml` 自动探测顺序；
     - 混合来源下的 MySQL 连接串拼装（密码含特殊字符与空格）及 `database.url` 覆盖规则；
     - 混合来源下的 SMTP `465` 端口自动 SSL 推导与 TLS/SSL 互斥校验；
     - 异常场景：显式 `CONFIG_FILE` 不存在、YAML 语法错误、根节点非 mapping、空 YAML 文件、未知键报错、布尔值误传端口/天数的拦截。

4. **补齐配套模板、忽略规则与部署文档**
   - 新增根目录 `config.example.yaml`（中文注释、脱敏默认值）。
   - 修改 [.gitignore](file:///d:/Projs/wing-trade/.gitignore)，忽略本地 `config.yaml`、`config.yml`、`data/config.yaml`、`data/config.yml`。
   - 修改 [compose.yaml](file:///d:/Projs/wing-trade/compose.yaml)，在 `app.environment` 中传递 `CONFIG_FILE: ${CONFIG_FILE:-}`。
   - 修改 [README.md](file:///d:/Projs/wing-trade/README.md)，补充 YAML 配置结构、优先级及 Docker Compose 使用说明。

## 2. 校验命令

```bash
# 代码风格检查
uv run --cache-dir .uv-cache --project backend ruff check backend

# 类型检查
uv run --cache-dir .uv-cache --project backend mypy backend/app

# 后端单元测试与集成测试
uv run --cache-dir .uv-cache --project backend pytest backend/tests
```

## 3. 关键风险点与检查门禁

- 确认所有新增/修改方法均有中文注释（遵守 `AGENTS.md` 要求）。
- 确认所有新增/修改文档均使用中文。
- 确认不删除、不清空本地 `ledger.db`。
- 确认 `config.example.yaml` 可直接被 `Settings.load("config.example.yaml")` 解析通过（或在测试中用示例模板校验其结构合法性，防止模板与代码漂移）。
