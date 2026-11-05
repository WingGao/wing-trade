# M0 账本领域层与数据库 (m0-ledger-domain-db) — Technical Design

## 1. Module Structure

```text
backend/
├── alembic.ini
├── pyproject.toml
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 0001_initial_ledger_schema.py
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── db/
│   │   ├── base.py              # DeclarativeBase + DecimalString TypeDecorator
│   │   └── session.py           # Engine, WAL pragma hook, Session / UoW helper
│   ├── domain/
│   │   ├── enums.py             # TradeSide, TradeStatus, LotStatus, CycleStatus, TargetStatus, CalcBasis, MatchingMethod
│   │   ├── exceptions.py        # DomainError hierarchy (InsufficientLotQtyError, LotHasActiveMatchesError, etc.)
│   │   ├── models.py            # Pure dataclasses for calculation inputs/outputs
│   │   └── precision.py         # Decimal quantize helpers: round_qty(4), round_price(6), round_amount(4)
│   ├── models/
│   │   └── ledger.py            # SQLAlchemy 2.x ORM models (Account, Security, WatchlistItem, Strategy, Cycle, Trade, Lot, TradeMatch, ProfitTarget, QuoteCache, AuditEvent)
│   └── services/
│       ├── accounting.py        # AccountingService: cashflows, allocations, position & cycle metrics
│       ├── matching.py          # MatchingService: FIFO preview, manual match validation, tail-rounding allocation
│       ├── cycle.py             # CycleService: open/close cycle, strategy breakeven price
│       ├── target.py            # TargetService: gross/net target price solver & qty conflict check
│       └── ledger.py            # TradeLedgerService: transactional buy, sell, void, audit logging
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_spec_section6_cases.py
    │   ├── test_invariants_property.py
    │   └── test_target_solver.py
    └── integration/
        └── test_ledger_service_db.py
```

## 2. Exact Decimal Storage & Tail-Rounding Algorithm

### 2.1 SQLite Decimal Storage (`DecimalType`)
SQLite 的 `NUMERIC` 亲和性会将十进制数转为 8 字节二进制浮点数，破坏账务精度。因此定义 `ExactDecimal(scale: int)`（基于 `TypeDecorator(String(32))`），入库前统一 `quantize` 为固定小数位字符串（如 `"1000.0000"`），出库时反序列化为 `Decimal`。所有大小比较在 Python 领域层或定长/数值转换下执行。

### 2.2 买卖成本与净额分摊算法（尾差归最后一段）
设一笔卖出成交 `sell`（数量 $Q_s$，含费净收入 $N_s = \text{round}_4(P_s \times Q_s) - F_s$）匹配了有序配对列表 $M = [(L_1, q_1), (L_2, q_2), \dots, (L_k, q_k)]$，其中 $\sum_{i=1}^k q_i = Q_s$：

1. **卖方净收入分摊（跨匹配项 $i = 1 \dots k$）**：
   - 当 $i < k$ 时：$\text{net}_i = \text{round}_4\left(N_s \times \frac{q_i}{Q_s}\right)$
   - 当 $i = k$ 时（最后一段吸收卖方尾差）：$\text{net}_k = N_s - \sum_{j=1}^{k-1} \text{net}_j$
2. **买方批次成本分摊（单批次 $L_i$ 维度）**：
   - 若 $q_i == L_i.\text{remaining\_qty}$（即本次匹配耗尽该批次剩余全部股数，为该 Lot 的最后一段）：
     $$\text{cost}_i = L_i.\text{remaining\_cost}$$
   - 若 $q_i < L_i.\text{remaining\_qty}$（部分耗尽）：
     $$\text{cost}_i = \text{round}_4\left(L_i.\text{original\_cost} \times \frac{q_i}{L_i.\text{original\_qty}}\right)$$
   - 更新后：$L_i.\text{remaining\_qty} \leftarrow L_i.\text{remaining\_qty} - q_i$，$L_i.\text{remaining\_cost} \leftarrow L_i.\text{remaining\_cost} - \text{cost}_i$。
3. **单段已实现收益**：
   $$\text{realized\_pnl}_i = \text{net}_i - \text{cost}_i$$

此设计严格保证 Spec §5.7 的全部 6 条不变量在任意复杂拆分下恒成立，不会出现 0.0001 元的悬空成本或收益漂移。

### 2.3 净收益目标价求解（`TargetService`）
给定批次单位含费成本 $c = L.\text{original\_cost} / L.\text{original\_qty}$、计划卖出数量 $q_p$、目标净收益率 $r$、预估费率配置（佣金率 $\alpha$、最低佣金 $C_{\min}$、印花税率 $\beta$、过户费率 $\gamma$）：
- 目标卖出净回收额：$N_{\text{target}} = c \cdot q_p \cdot (1 + r)$
- 对候选卖出价 $P$，总卖出毛额 $G(P) = P \cdot q_p$，预估卖出总费用 $F(P) = \max(G(P)\cdot \alpha, C_{\min}) + G(P)\cdot (\beta + \gamma)$，净额 $N(P) = G(P) - F(P)$。
- 因 $N(P)$ 关于 $P$ 严格单调递增且分段线性，直接解析求解两段（佣金按比例 vs 佣金触发最低标准 $C_{\min}$）并向上量化到 `0.0001`（或 A 股最小报价单位 `0.01` 参考值），即可在 $O(1)$ 时间内得到精确目标价。
