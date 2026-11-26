# 实施计划：个股详情页策略表与 Markdown 导入

## 1. 实施顺序清单

### 阶段 A：后端数据模型、迁移与服务层
1. 在 `backend/app/domain/enums.py` 新增 `StrategyRowStatus` 枚举（`PENDING` / `BOUGHT` / `COMPLETED`）。
2. 在 `backend/app/models/ledger.py` 新增 ORM 模型 `SecurityStrategyRow`，并在 `backend/app/models/__init__.py` 导出。
3. 新增 Alembic 迁移 `backend/alembic/versions/0009_security_strategy_rows.py`（`down_revision = "0008_watchlist_buyback_alerts"`），创建 `security_strategy_rows` 表与复合索引。
4. 在 `backend/app/schemas/` 中新增策略表读写与导入请求/响应 Schema（含正数校验与非全空行校验）。
5. 新增 `backend/app/services/strategy_plan.py` 服务模块，实现查询、批量导入（覆盖/追加）、新增、局部更新、删除、清空及用户隔离校验。
6. 在 `backend/app/api/v1/router.py` 注册 6 个策略表 REST 接口。

### 阶段 B：后端测试
7. 新增集成测试 `backend/tests/integration/test_security_strategy_api.py`，覆盖：
   - 查询空表、批量导入（覆盖/追加）、单行增删改、状态切换、清空；
   - 跨账户同标的共享、不同用户严格隔离；
   - 非法数值（<=0）、全空行、不存在 `security_id` 等异常校验；
   - 数据库迁移启动兼容性验证（独立临时库，不碰本地 `ledger.db`）。

### 阶段 C：前端解析器、API 与组件实现
8. 在 `frontend/src/utils/strategyMarkdown.ts` 实现：
   - 固定静态 AI 提示词常量 `STRATEGY_TABLE_AI_PROMPT`；
   - `parseStrategyMarkdown` 解析与校验函数；
   - 剪贴板复制辅助函数（含降级兜底）。
9. 在 `frontend/src/api/types.ts` 与 `frontend/src/api/client.ts` 新增策略表类型定义与 API 方法。
10. 新增前端组件：
    - `frontend/src/components/StrategyRowDialog.vue`（单行新增/编辑弹窗）
    - `frontend/src/components/StrategyMarkdownImportDialog.vue`（Markdown 粘贴解析预览与提示词展示/复制弹窗）
    - `frontend/src/components/SecurityStrategyCard.vue`（桌面端表格 + 移动端高密度卡片 + 现价触达高亮 + 快捷改状态 + 提示词复制/预览）
11. 在 `frontend/src/components/SecurityDetailPanel.vue` 中挂载 `SecurityStrategyCard`（桌面端与移动端视图均接入）。

### 阶段 D：前端验证与全量质量检查
12. 编写针对 `strategyMarkdown.ts` 的纯函数单元测试（通过 Node / TypeScript 验证各类正常与异常 Markdown 输入）。
13. 运行后端 `pytest`、前端 `npm run type-check` 与 `npm run build`、以及 `trellis-check` 质量检查。

## 2. 验证命令

- 后端测试：
  ```bash
  cd backend && pytest tests/integration/test_security_strategy_api.py tests/integration/test_database_startup.py
  ```
- 前端类型检查与构建：
  ```bash
  cd frontend && npm run type-check && npm run build
  ```
- Markdown 解析逻辑单测：
  - 使用 node 运行解析测试用例（覆盖标准 5 列、别名列、代码块包裹、货币符号/千分位/单位清洗、单边留空、非法数值报错等）。

## 3. 风险点与注意事项

- **严禁触碰本地 `ledger.db`**：所有后端测试均使用内存或临时测试库，绝不清空或覆盖本地数据库文件。
- **注释规范**：所有新增的 Python 函数/方法/类与 TypeScript 函数/组件方法必须编写清晰的中文注释。
- **移动端零横向溢出**：在 `SecurityStrategyCard.vue` 的 ≤ 768px 视图中，按钮组、备注长文本与价格数字需做好换行或截断，严格遵守 `.trellis/spec/frontend/mobile-high-density.md`。
- **与 `embedded` 模式兼容**：首页桌面端右栏内嵌模式宽度较窄，表格列宽与顶部工具栏需支持 `flex-wrap` 自动折行，避免挤压变形。
