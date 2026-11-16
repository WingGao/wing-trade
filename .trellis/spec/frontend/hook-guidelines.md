# Frontend Hook Guidelines

## Current Pattern

This project uses Vue Composition API functions directly in script setup rather
than a separate composable directory. Use onMounted for initial page loading,
ref for local form state, computed for derived display values, and watch only
when a dependency change must reload data.

## Data Fetching

Call the typed functions in src/api/client.ts from a page or Pinia store. Catch
errors at a boundary that can show ElMessage or an ElAlert. Quote failures must
be isolated so ledger pages remain usable.

## Naming

Use verb-based async functions such as load, refresh, save, and submit. If a
shared composable is added later, name it useSomething and keep its return type
explicit.

## Common Mistakes

Do not duplicate fetch URL construction in components, start requests during
render, or treat a failed quote request as a failed ledger request.

## Dashboard 刷新生命周期

- 使用页面级 `pageAlive`、请求代次与单个 timer 管理自动刷新；任何新请求开始前先清理旧 timer。
- 请求完成后才按服务器 `pollAfterSeconds` 安排下一次计时，不能在发起时并行叠加计时器。
- `visibilitychange` 隐藏时暂停、可见时恢复；`onBeforeUnmount` 必须清理 timer 和监听器。
- 手动刷新、自动刷新和交易后重载统一走同一 Dashboard 加载入口，由 store 负责串行与同查询复用。
- 服务器返回的行情/估值状态是唯一事实源，页面不得自行推断交易时段或重算浮盈。
