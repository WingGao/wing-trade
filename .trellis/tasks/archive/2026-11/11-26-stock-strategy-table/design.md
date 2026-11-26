# 技术设计：个股详情页策略表与 Markdown 导入

## 1. 架构与边界

本功能横跨后端持久层/API 层与前端 UI/解析层，保持与现有账务（`Trade` / `Lot` / `Strategy`）和关注（`WatchlistItem`）模块解耦：

- **后端**：新增独立数据表 `security_strategy_rows` 及配套 ORM 模型、Pydantic Schema、Service 与 REST API，挂在 `/api/v1/securities/{security_id}/strategy-rows` 下，复用现有 `get_current_user` 鉴权依赖与 `DomainError` 异常处理体系。
- **前端**：
  - Markdown 表格解析与固定 AI 提示词作为纯函数/常量模块放在 `frontend/src/utils/strategyMarkdown.ts`，便于单独单元测试。
  - API 方法扩展到 `frontend/src/api/types.ts` 和 `frontend/src/api/client.ts`。
  - 策略表卡片及弹窗拆分为独立组件，嵌入 `frontend/src/components/SecurityDetailPanel.vue`，从而自动同时生效于独立详情页与首页右栏内嵌模式。

## 2. 数据库模型与迁移

### 2.1 新增表 `security_strategy_rows`

避免与现有持仓策略分组表 `strategies` 混淆，新表命名为 `security_strategy_rows`：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | `String(36)` | PK, UUID | 行主键 |
| `user_id` | `String(36)` | FK(`users.id`, CASCADE), NOT NULL | 所属用户 |
| `security_id` | `String(36)` | FK(`securities.id`, CASCADE), NOT NULL | 所属标的 |
| `sort_order` | `Integer` | NOT NULL, default `0` | 展示顺序 |
| `buy_price` | `ExactDecimal(6)` | NULL | 买入价（> 0） |
| `buy_qty` | `ExactDecimal(4)` | NULL | 买入数量（> 0） |
| `sell_price` | `ExactDecimal(6)` | NULL | 卖出价（> 0） |
| `sell_qty` | `ExactDecimal(4)` | NULL | 卖出数量（> 0） |
| `note` | `String(256)` | NULL | 备注 |
| `status` | `String(16)` | NOT NULL, default `'PENDING'` | `PENDING` / `BOUGHT` / `COMPLETED` |
| `created_at` | `DateTime(timezone=True)` | NOT NULL | 创建时间 |
| `updated_at` | `DateTime(timezone=True)` | NOT NULL | 更新时间 |

- **索引**：`Index("ix_security_strategy_rows_user_security_order", "user_id", "security_id", "sort_order")`。
- **Alembic 迁移**：新增 `backend/alembic/versions/0009_security_strategy_rows.py`，`down_revision = "0008_watchlist_buyback_alerts"`，纯增量 `create_table` + `create_index`，无损兼容现有 `ledger.db`。

### 2.2 领域枚举

在 `backend/app/domain/enums.py` 新增 `StrategyRowStatus(StrEnum)`：
- `PENDING = "PENDING"`（待执行）
- `BOUGHT = "BOUGHT"`（已买入）
- `COMPLETED = "COMPLETED"`（已完成）

## 3. 后端 API 契约

所有接口均要求已登录用户（`Depends(get_current_user)`），并校验 `security_id` 存在（不存在返回 `404`）以及行归属当前用户（越权返回 `404`）。字段遵循现有 `camelCase` JSON 序列化规范：

1. **查询策略表**
   - `GET /api/v1/securities/{security_id}/strategy-rows`
   - 返回：`SecurityStrategyRowRead[]`（按 `sortOrder ASC, createdAt ASC` 排序）。
2. **批量导入策略行（覆盖或追加）**
   - `POST /api/v1/securities/{security_id}/strategy-rows/import`
   - 请求体：
     ```json
     {
       "mode": "replace", // "replace" | "append"
       "rows": [
         {
           "buyPrice": "12.50",
           "buyQty": "1000",
           "sellPrice": "13.80",
           "sellQty": "1000",
           "note": "第一档支撑位",
           "status": "PENDING"
         }
       ]
     }
     ```
   - 行为：在同一数据库事务中执行；`replace` 模式先删除当前用户该标的旧行再批量写入新行，`append` 模式从现有最大 `sortOrder + 1` 起追加。返回导入后的完整 `SecurityStrategyRowRead[]`。
3. **新增单行**
   - `POST /api/v1/securities/{security_id}/strategy-rows`
   - 请求体：单行字段，默认追加到末尾，返回 `SecurityStrategyRowRead`。
4. **更新单行（编辑字段或快捷改状态）**
   - `PATCH /api/v1/securities/{security_id}/strategy-rows/{row_id}`
   - 请求体：局部更新字段（使用 `model_fields_set` 区分未传与显式置空 `null`），校验更新后行不能全空，返回 `SecurityStrategyRowRead`。
5. **删除单行**
   - `DELETE /api/v1/securities/{security_id}/strategy-rows/{row_id}`
   - 返回：`204 No Content`。
6. **清空策略表**
   - `DELETE /api/v1/securities/{security_id}/strategy-rows`
   - 删除当前用户该标的全部策略行，返回：`204 No Content`。

### 校验规则（后端兜底）

- `buyPrice` / `buyQty` / `sellPrice` / `sellQty` 若非空，必须为有限正数（`> 0`）。
- 单行至少包含 `buyPrice`、`buyQty`、`sellPrice`、`sellQty` 中的一项（禁止全空行）。
- `note` 去除首尾空白后最大长度 256 字符，空字符串归一化为 `null`。
- `import` 单次最多允许 200 行，防止异常大文本写入。

## 4. 前端设计与数据流

### 4.1 Markdown 解析与固定提示词模块（`frontend/src/utils/strategyMarkdown.ts`）

- **固定静态 AI 提示词常量 `STRATEGY_TABLE_AI_PROMPT`**：
  - 纯静态中文提示词，指导 AI 根据当前对话中的标的分析，输出严格包含以下 5 列的 Markdown 表格：
    ```markdown
    | 买入价 | 买入数量 | 卖出价 | 卖出数量 | 备注 |
    | --- | --- | --- | --- | --- |
    ```
  - 明确要求：价格和数量尽量输出纯数字（不带货币符号和文字区间，若单边无计划用 `-` 留空），每行代表一组买卖对应档位，备注简明写明触发逻辑或仓位说明。
- **解析函数 `parseStrategyMarkdown(rawText: string)`**：
  - 预处理：若文本包含 ```` ``` ```` 代码块，优先扫描并提取首个符合 Markdown 表格结构（表头行 + `---` 分隔行 + 数据行）的区块；否则直接从全文提取首个表格区块。
  - 列映射：
    1. 按中英文关键词标准化匹配表头（如含 `买` + `价` → `buyPrice`，含 `买` + `量/股/份` → `buyQty`，含 `卖` + `价` → `sellPrice`，含 `卖` + `量/股/份` → `sellQty`，含 `备注/说明/条件/理由/档位` → `note`，含 `状态` → `status`）。
    2. 若表头包含独立的“档位”列且另有“备注”列，将档位与备注合并为 `档位：备注` 写入 `note`，不丢失信息。
    3. 若表头未能识别任何已知列名且列数在 4~5 列，按顺序回退映射 `[buyPrice, buyQty, sellPrice, sellQty, note?]` 并返回提示信息。
  - 单元格清洗：去除 `**` / `` ` ``、货币符号（`¥￥$`）、千分位 `,`、末尾单位（`股` / `份`）；`-` / `—` / `--` / `无` / `空` / `N/A` 识别为 `null`。
  - 返回结构：`{ rows: StrategyRowDraft[], errors: StrategyParseError[], warnings: string[] }`，供导入弹窗实时渲染预览表与精确行号报错。

### 4.2 前端组件拆分

- `frontend/src/components/SecurityStrategyCard.vue`：
  - Props：`securityId: string`、`currency: Currency`、`isFund: boolean`、`quote?: Quote | null`、`embedded?: boolean`。
  - 负责加载当前标的策略行、维护 loading/error 状态、渲染桌面端 `el-card` + `el-table` 与移动端 `.m-section` 紧凑卡片列表。
  - 实现现价高亮计算：
    - `PENDING` 且 `lastPrice <= buyPrice` → 买入价高亮标签（`已到买价`）。
    - `PENDING | BOUGHT` 且 `lastPrice >= sellPrice` → 卖出价高亮标签（`已到卖价`）。
  - 顶部按钮：复制 AI 提示词（带查看提示词小入口）、导入 Markdown、新增档位、清空。
- `frontend/src/components/StrategyMarkdownImportDialog.vue`：
  - 提供多行文本输入框、内置“复制 AI 提示词 / 展开查看提示词”折叠区、导入模式切换（覆盖当前 / 追加末尾）、实时解析预览表与错误列表。
- `frontend/src/components/StrategyRowDialog.vue`：
  - 新增 / 编辑单行策略档位的表单弹窗（买入价、买入数量、卖出价、卖出数量、执行状态、备注）。
- 接入 `frontend/src/components/SecurityDetailPanel.vue`：
  - 在桌面端持仓批次 `el-card` 下方、移动端持仓批次 `.m-section` 下方挂载 `SecurityStrategyCard`（只要 `security?.id` 存在即展示，不依赖 `accountId`）。

## 5. 关键权衡与风险

- **解析放在前端 vs 后端**：将 Markdown 解析放在前端纯函数模块，可实现粘贴即实时预览、零网络延迟反馈错误行号；后端接口只接收结构化 JSON 并做严格数值/权限校验，职责边界清晰且易于扩展其他导入源。
- **不与现有关联账户绑定**：切账户时不触发策略表重载，仅在 `securityId` 变化时重载，避免多余请求。
- **回滚策略**：新增表 `security_strategy_rows` 与新增路由完全独立，若需回滚只需移除前端挂载与路由注册，不影响任何既有账务或行情表；Alembic `downgrade()` 仅删除新建表。
