# M0 账本 REST API (m0-ledger-api) — Implementation Plan

## 1. Prerequisite

- 必须先完成并通过 `11-05-m0-ledger-domain-db` 的全部测试。

## 2. Ordered Checklist

- [ ] **Step 1: 实现 Pydantic v2 Schemas 与统一错误处理器 (`backend/app/api/schemas/`, `errors.py`)**
- [ ] **Step 2: 实现证券、自选股与默认账户/策略初始化接口 (`securities.py`, `watchlist.py`)**
- [ ] **Step 3: 实现买入、卖出预览、卖出提交与撤销接口 (`trades.py`)**
- [ ] **Step 4: 实现持仓、批次、配对记录、周期、止盈目标与统计汇总接口 (`positions.py`, `targets.py`, `analytics.py`)**
- [ ] **Step 5: 实现结构化日志中间件、`/health`、`/ready` 及 API 集成测试 (`backend/tests/integration/test_api_v1.py`)**

## 3. Validation Commands

```bash
cd backend
pytest tests/integration/test_api_v1.py -v
ruff check app tests
mypy app
```
