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

后端依赖 Python 3.12，运行 `uvicorn app.main:app --reload --app-dir backend`。前端开发服务器会把 `/api` 代理到 `localhost:8000`。

## 质量检查

```bash
cd frontend
pnpm run type-check
pnpm run build
npx playwright test
```

行情是派生数据。行情源不可用时，前端显示“行情不可用”或“已过期”，账务录入和历史查询仍可用；前端不会自动下单或修改服务端核算结果。
