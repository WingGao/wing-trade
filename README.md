# A 股个人交易账本

这是一个以交易流水为事实源的自托管 A 股投资账本。后端提供批次配对、收益核算和共享行情缓存，前端提供总览、个股配对、交易流水与复盘工作台。

## 一键启动

```bash
docker compose up --build
```

打开 <http://localhost:8080>；后端健康检查位于 <http://localhost:8000/ready>。SQLite 数据库持久化在 `./data/ashare_journal.db`，备份时停止服务后复制 `data/` 目录即可。

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
