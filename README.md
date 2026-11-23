# A 股个人交易账本

这是一个以交易流水为事实源的自托管 A 股投资账本。后端提供批次配对、收益核算和共享行情缓存，前端提供总览、个股配对、交易流水与复盘工作台。

## 一键启动

```bash
docker compose up --build
```

打开 <http://localhost:8080>（可通过环境变量 `APP_PORT` 调整宿主机端口）；健康检查位于 <http://localhost:8080/ready>。容器构建时会先编译前端静态资源并由后端 FastAPI 统一提供服务；未配置 `config.yaml` 或 `.env` 时保持 SQLite 默认行为，数据库文件仍持久化在 `./data/ashare_journal.db`。

## 配置文件（YAML 优先，其次环境变量）

后端支持通过结构化 YAML 文件进行配置，优先级按字段粒度生效：**YAML 配置 > 环境变量（env）> 内置默认值**。YAML 中未填写或设为 `null` 的字段会自动回退到对应的环境变量或默认值。

- 脱敏模板：[config.example.yaml](./config.example.yaml)（本地 `config.yaml`、`config.yml` 及 `data/config.yaml`、`data/config.yml` 均已被 `.gitignore` 忽略）。
- 本地开发直接复制模板即可生效：

```bash
cp config.example.yaml config.yaml
```

- 配置文件查找顺序：
  1. 显式环境变量 `CONFIG_FILE`（指定路径不存在或格式非法时会直接报错终止启动）；
  2. 当前工作目录下的 `config.yaml` / `config.yml`；
  3. 项目根目录下的 `config.yaml` / `config.yml`；
  4. 以上均不存在时无缝回退到纯环境变量与默认值。
- Docker Compose 使用 YAML 配置时，可将配置文件放入已挂载的数据目录 `./data/config.yaml`，并在 `.env` 中设置：

```bash
CONFIG_FILE=/app/data/config.yaml
```

## 数据库配置

根目录 `.env` 只用于本机配置，该文件已被 `.gitignore` 忽略；可提交的脱敏模板是 `.env.example`。Docker Compose 会自动读取根目录 `.env`，且容器内会把 MySQL 主机切换到 Compose 服务名 `mysql`。

切换 MySQL 8.4 时，复制模板并修改数据库名、用户和密码：

```bash
cp .env.example .env
docker compose up --build
```

MySQL 配置项如下：

```bash
COMPOSE_PROFILES=mysql
DATABASE_BACKEND=mysql
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=ashare_ledger
MYSQL_USER=ashare
MYSQL_PASSWORD='change-me@2026'
```

密码可直接填写，不需要手工做 URL 编码。`.env` 中包含特殊字符时，建议用单引号包裹。MySQL 数据保存在 Compose 命名卷 `mysql_data` 中；后端使用 PyMySQL、`utf8mb4`、连接前探活和 1800 秒连接回收。

`COMPOSE_PROFILES=mysql` 只在 Docker Compose 中启用 MySQL 服务。改用 SQLite 时将 `COMPOSE_PROFILES` 留空并设置 `DATABASE_BACKEND=sqlite`，Compose 就不会启动或等待 MySQL。显式设置 `DATABASE_URL` 时优先级最高，会覆盖 `DATABASE_BACKEND` 与 `MYSQL_*` 组合配置。未设置 `DATABASE_BACKEND` 或设置为 `sqlite` 时，后端继续使用 SQLite；Compose 模式仍使用 `./data/ashare_journal.db`，直接运行后端时仍默认使用根目录 `ledger.db`。

后端启动时会自动准备数据库结构：空库执行 `alembic upgrade head`，已版本化数据库升级尚未应用的 revision；旧版仅由 `create_all` 建立且没有 `alembic_version` 的数据库会先补齐缺失表并标记当前版本，不会删除、清空或重写既有账务数据。因此只配置根目录 `.env` 后运行 `docker compose up --build` 即可完成首次启动。

如需在容器内手动检查或执行迁移：

```bash
docker compose run --rm app alembic upgrade head
```

该命令是幂等的，后续结构升级也可使用。SQLite 数据不会自动迁移到 MySQL；切换前应停止服务并备份 `data/` 中的数据库文件。不要删除或清空现有 `ledger.db`，也不要让迁移命令指向错误数据库。

## 本地开发

```bash
cd frontend
pnpm install
pnpm run dev
```

后端依赖 Python 3.12，使用 `uv run --cache-dir .uv-cache --project backend --with "uvicorn[standard]>=0.30,<1.0" python -m uvicorn app.main:app --reload --app-dir backend` 启动。前端开发服务器会把 `/api` 代理到 `localhost:8000`。

## 行情配置

后端默认使用东方财富公开 A 股批量行情接口。可通过环境变量调整：

```bash
QUOTE_PROVIDER=eastmoney
QUOTE_TIMEOUT_SECONDS=5
```

`QUOTE_PROVIDER` 支持 `eastmoney` 和 `mock`。离线开发或公网行情不可用时，设置 `QUOTE_PROVIDER=mock` 并重启后端；已有账务数据和最后一次有效报价都会保留。每次上游请求超时由 `QUOTE_TIMEOUT_SECONDS` 控制，公开行情源本身不保证实时性或可用性。

首页行情使用共享缓存和熔断退避：交易时段通常每 10 秒检查一次，休市时每 60 秒检查展示状态，上游实际刷新会受最小间隔和退避限制。行情失败只会把报价和估值标为过期或不可用，不会阻塞记账。持仓浮盈金额使用“剩余数量 × 最新价 − 剩余成本”计算，百分比仅在剩余成本大于零时返回；剩余成本包含买入费用，不扣未来预计卖出费用。

## 质量检查

```bash
cd frontend
pnpm run type-check
pnpm run build
npx playwright test
```

行情是派生数据。行情源不可用时，前端显示“行情不可用”或“已过期”，账务录入和历史查询仍可用；前端不会自动下单或修改服务端核算结果。
