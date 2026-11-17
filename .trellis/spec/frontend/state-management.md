# Frontend State Management

## State Categories

Pinia stores own reusable server and preference state. Local refs belong in a
page or dialog when no other component needs them. Route params are URL state.
The current stores are ledger, quotes, and preferences.

## Store Rules

The ledger store loads positions, analytics summary, and trades. The quotes store
owns quote loading, refresh state, and lookup by security ID. The preferences
store owns includeClosed and display color preferences. Keep exact monetary
strings from the API in state; do not normalize them through Number for requests.

## Server State

Use the typed API client for reads and mutations. Pages call store load methods
in onMounted and reload after a successful mutation. Quote load errors are
swallowed or shown as stale state so ledger actions remain available.

## Derived State

Use computed values for filtered rows and display-only labels. A derived value
must point back to server state and must not reimplement accounting or matching.

## Scenario: Dashboard 一致估值状态

### 1. Scope / Trigger

- 触发条件：首页使用 `/api/v1/dashboard`，或修改行情、持仓浮盈、账户汇总与刷新生命周期。
- 价格、持仓浮盈、最低价批次组和账户汇总必须来自同一份 `DashboardRead`，不能在多个 store 请求后临时拼接。

### 2. Signatures

- `api.dashboard(accountId?: string, includeClosed = false, mode: DashboardMode = 'auto'): Promise<DashboardRead>`
- `useLedgerStore().loadDashboard({ accountId?, includeClosed?, mode?, reuse? }): Promise<DashboardRead | null>`
- `DashboardRead` 至少包含 `accountId`、`currency`、`asOf`、`pollAfterSeconds`、`positions`、`summary` 和 `quotes`。

### 3. Contracts

- 首次进入先加载 `mode=cache` 并展示快照，再加载 `mode=auto`；请求成功后整份替换首页状态。
- Pinia 保留服务端精确十进制字符串；组件只格式化显示，不重新计算浮盈、收益率或账户汇总。
- 自动刷新按服务端 `pollAfterSeconds` 调度；标签页隐藏时暂停，恢复可见后立即刷新；卸载时清理计时器和监听器。
- 自动刷新复用同查询的在途请求；手动刷新与自动刷新串行执行；旧代次响应不得覆盖新查询或新页面状态。
- 快速买入/卖出成功后使用当前 `includeClosed` 重载整份 Dashboard，不只刷新行情或持仓子集。
- `partial|unavailable` 时不展示局部账户总浮盈；`stale` 可展示带文字状态的参考估值；`null`、零和零涨幅必须区分。

### 4. Validation & Error Matrix

| 条件 | 结果 |
|---|---|
| cache 成功、auto 在途 | 先展示 cache，再整份替换为 auto |
| auto 与手动刷新并发 | 只允许一个业务请求在途，后发请求排队或复用 |
| 页面隐藏 | 取消计时器，不安排新的自动请求 |
| 页面卸载 | 清理 timer 和 `visibilitychange` 监听器，旧页面代次失效 |
| 切换 includeClosed | 使用新参数重新读取整份 Dashboard |
| Dashboard 请求失败 | 保留最后一致快照并标记过期/不可用，账务操作仍可用 |
| `valuationStatus=partial` | 账户未实现收益显示不可用，不显示部分小计 |

### 5. Good/Base/Bad Cases

- Good：服务器返回价格 10、浮盈 0、收益率 0；页面显示 `0.00` 和 `0.00%`。
- Base：行情接口失败但已有 cache；页面显示明确过期状态，快速记账仍可用。
- Bad：价格使用新 quotes store、浮盈使用旧 ledger store，形成不同估值时间的数据拼接。
- Bad：用 `Number` 重算金额或把 `null` 默认成零。

### 6. Tests Required

- E2E 断言 cache 先于 auto 可见、auto 返回后价格与浮盈同步替换。
- E2E 断言轮询请求不重叠、隐藏页不新增请求、恢复可见后继续刷新。
- E2E 断言交易成功后的重载保留 `includeClosed=true`。
- E2E 断言零涨幅、过期、缺失与 partial 汇总的文案和值，不出现局部总额。
- type-check/build 必须通过；e2e 行情响应全部由路由 mock 提供。

### 7. Wrong vs Correct

#### Wrong

```ts
positions.value = await api.positions(includeClosed)
summary.value = await api.summary()
quotes.value = await api.quotes()
```

#### Correct

```ts
const snapshot = await api.dashboard(accountId, includeClosed, mode)
dashboard.value = snapshot
```

## Scenario: 卖出预览失效与确认一致性

### 1. Scope / Trigger

- 触发条件：修改卖出配对工作台、预览请求状态，或预览到正式卖出的提交契约。
- 工作台只展示后端配对结果；输入对象变化后必须重新预览，旧异步响应不能恢复确认能力。

### 2. Signatures

- `api.previewSell(payload)` 工作台请求包含 `matchingMethod: EQUAL`，服务端仍兼容 FIFO/LIFO。
- `SellPreview` 包含 `currency`、`executedAt`、金额字段和 `SellPreviewMatch[]`。
- `SellPreviewMatch` 包含真实买入信息、可用数量、本次配对数量和服务端分摊结果。

### 3. Contracts

- 当前工作台显式请求 `EQUAL`：最近买入的最小覆盖集合内按容量等量分配；账户、证券、数量、价格或成交时间变化后立即清空预览并推进请求代次。
- 响应只有在代次仍为当前代次时才能写入；失败或过期响应不得重新启用“确认卖出”。
- 确认按钮仅在存在预览、预览输入快照与当前输入完全一致且没有预览请求在途时启用。
- 提交使用预览响应的标准化数量、价格、成交时间及明确批次；按钮标签和展示文案不得再写 FIFO。
- 主要展示买入时间、原始成交价和本次配对数量；原始数量、预览前可用数量与短批次号作为辅助信息，完整编号保留在提示中。

### 4. Validation & Error Matrix

| 条件 | 必须结果 |
|---|---|
| 数量、价格或成交时间无效 | 阻止请求并给出提示 |
| 输入在请求期间变化 | 旧预览清空，旧响应被忽略 |
| 预览失败 | 保持无预览状态，确认按钮禁用 |
| 账户或证券切换 | 旧预览失效并提升请求代次 |
| 预览成功但输入已变化 | 不展示旧结果，不启用确认 |

### 5. Good / Base / Bad Cases

- Good：先请求 6 股慢响应，再修改为 7 股并成功预览；6 股响应到达后页面仍保留 7 股预览。
- Base：用户修改任一表单字段后，原确认按钮立即禁用并显示重新预览提示。
- Bad：只用 `loading=false` 判断旧请求结束，导致旧响应覆盖新输入。

### 6. Tests Required

- E2E 断言请求包含 `matchingMethod=EQUAL`，并展示买入时间、原始成交价、配对数量和可用数量。
- E2E 制造两次乱序预览响应，断言旧响应不能覆盖新预览或启用确认。
- E2E 断言确认请求携带当前预览的明确 `lotId` 与 `matchedQty`。
- `pnpm run type-check`、`pnpm run build` 和相关 Playwright 用例必须通过。

### 7. Wrong vs Correct

#### Wrong

```ts
preview.value = await api.previewSell(payload)
```

#### Correct

```ts
const generation = ++previewGeneration
const result = await api.previewSell(payload)
if (generation === previewGeneration) preview.value = result
```
