import { expect, test, type Page, type Route } from '@playwright/test'
import type { DashboardRead, Lot, PositionRefreshScope, Trade, TradeUpdate } from '../src/api/types'

const security = { id: 'security-edit', market: 'SH', symbol: '600519', name: '贵州茅台', currency: 'CNY' as const }
const accountId = 'account-primary'
const strategyId = 'strategy-primary'
const quote = { symbol: 'SH:600519', securityId: security.id, lastPrice: '11.000000', isStale: false, currency: 'CNY' as const }

/** 输出完整模拟响应，所有交易与行情请求都在浏览器内拦截。 */
async function respond(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/** 保留价格、数量、费用和时间的原始精度作为编辑基线。 */
function makeTrade(): Trade {
  return { id: 'trade-edit', accountId, securityId: security.id, strategyId, side: 'BUY', executedAt: '2026-11-17T01:30:45.123456Z', qty: '100.1234', price: '10.123456', grossAmount: '1013.5949', feesTotal: '1.1110', commission: '0.1234', stampDuty: '0.2345', transferFee: '0.3456', otherFee: '0.4075', memo: '原始备注', revision: 7, status: 'ACTIVE', market: security.market, symbol: security.symbol, name: security.name }
}

/** 构造派生批次，重算前后数量由测试显式控制。 */
function makeLot(qty = '100.1234'): Lot {
  return { id: 'lot-edited', buyTradeId: 'trade-edit', accountId, securityId: security.id, strategyId, cycleId: 'cycle-edit', originalQty: qty, remainingQty: qty, originalCost: '1014.7059', remainingCost: '1014.7059', unitCost: '10.134000', executedAt: makeTrade().executedAt, status: 'OPEN', targets: [], unrealizedPnl: '86.6515', unrealizedReturnPct: '8.5' }
}

/** 提供可变账务状态与失败开关，覆盖编辑、两处入口和竞态响应。 */
async function mockTrading(page: Page) {
  const state = {
    trade: makeTrade(), dirty: true, noActiveRows: false, conflict: false, recalculateFails: false, lotsFail: false, listFailsAfterSave: false,
    edits: [] as TradeUpdate[], recalculations: [] as URL[], lotsRequests: [] as URL[], dashboardRequests: [] as URL[], pollAfterSeconds: 60,
    currentQty: '100.1234', heldLots: null as Route | null, holdNextLots: false,
  }
  /** 生成当前账户快照，待刷新清单不依赖活动行。 */
  function snapshot(): DashboardRead {
    const scope: PositionRefreshScope = { accountId, securityId: security.id, strategyId, market: security.market, symbol: security.symbol, name: security.name }
    return {
      accountId, currency: 'CNY', asOf: '2026-11-18T02:00:00Z', marketSessionOpen: true, pollAfterSeconds: state.pollAfterSeconds,
      summary: { currency: 'CNY', marketValue: '1100', totalBuyCashOut: '1014.7059', totalSellCashIn: '0', realizedPnl: '0', unrealizedPnl: '86.6515', unrealizedReturnPct: '8.5', remainingQty: state.currentQty, remainingCost: '1014.7059', valuationStatus: 'fresh', totalOpenPositions: state.noActiveRows ? 0 : 1, quotedOpenPositions: 1, staleOpenPositions: 0 },
      positions: state.noActiveRows ? [] : [{ securityId: security.id, strategyId, market: security.market, symbol: security.symbol, name: security.name, remainingQty: state.currentQty, remainingCost: '1014.7059', averageCost: '10.134', realizedPnl: '0', unrealizedPnl: '86.6515', unrealizedReturnPct: '8.5', quote, lowestPriceGroup: null, needsRefresh: state.dirty }],
      quotes: [quote], pendingRefreshScopes: state.dirty ? [scope] : [],
    }
  }
  /** 根据请求范围返回状态，另一账户保持无标记，防止页面沿用旧提示。 */
  function lotsResponse(url: URL, dirty = state.dirty, qty = state.currentQty) {
    const requestedAccount = url.searchParams.get('accountId') ?? accountId
    return { accountId: requestedAccount, strategyId: url.searchParams.get('strategyId') ?? strategyId, needsRefresh: requestedAccount === accountId && dirty, lots: state.noActiveRows ? [] : [{ ...makeLot(qty), accountId: requestedAccount }], closedLotCount: state.noActiveRows ? 1 : 0, realizedPnl: '0', quote, currency: 'CNY' }
  }
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    const method = route.request().method()
    if (path === '/auth/me') return respond(route, { user: { id: 'admin-1', email: 'admin@localhost', displayName: '默认管理员', role: 'ADMIN', status: 'ACTIVE', isDefaultAdmin: true, createdAt: '2026-11-18T00:00:00Z' } })
    if (path === '/accounts') return respond(route, [{ id: accountId, name: '主账户', currency: 'CNY' }, { id: 'account-secondary', name: '另一个账户', currency: 'CNY' }])
    if (path === '/securities') return respond(route, [security])
    if (path === '/dashboard') { state.dashboardRequests.push(url); return respond(route, snapshot()) }
    if (path === '/trades') {
      if (state.listFailsAfterSave && state.edits.length) return respond(route, { error: { message: '流水读取暂不可用' } }, 503)
      return respond(route, [state.trade, { ...state.trade, id: 'voided-trade', status: 'VOIDED', side: 'SELL' }])
    }
    if (path === '/trades/trade-edit' && method === 'PATCH') {
      const payload = route.request().postDataJSON() as TradeUpdate
      state.edits.push(payload)
      if (state.conflict) return respond(route, { error: { code: 'TRADE_REVISION_CONFLICT', message: '交易已由其他操作修改，请重新加载后重试' } }, 409)
      Object.assign(state.trade, payload, { revision: state.trade.revision + 1 })
      state.dirty = true
      return respond(route, { trade: state.trade, needsRefresh: true })
    }
    if (path.endsWith('/intraday')) return respond(route, { symbol: quote.symbol, currency: 'CNY', tradingDate: '2026-11-18', points: [{ time: '2026-11-18T02:00:00Z', price: '11' }], quote, provider: 'fixture', fetchedAt: '2026-11-18T02:00:00Z', isStale: false, error: null })
    if (path.endsWith('/lots')) {
      state.lotsRequests.push(url)
      if (state.holdNextLots) { state.holdNextLots = false; state.heldLots = route; return }
      if (state.lotsFail) return respond(route, { error: { message: '批次读取暂不可用' } }, 503)
      return respond(route, lotsResponse(url))
    }
    if (path.endsWith('/recalculate')) {
      state.recalculations.push(url)
      if (state.recalculateFails) return respond(route, { error: { code: 'INSUFFICIENT_LOT_QTY', message: '历史卖出数量超过买入' } }, 409)
      state.dirty = false
      state.currentQty = '200.0000'
      return respond(route, { rebuiltLots: 1, rebuiltMatches: 0, needsRefresh: false })
    }
    if (path.endsWith('/profit-settings')) return respond(route, null)
    return respond(route, [])
  })
  return { state, lotsResponse, snapshot }
}

test('流水编辑完整预填且只提交改动，取消不写入、已撤销不可编辑', async ({ page }) => {
  const { state } = await mockTrading(page)
  await page.goto('/trades')
  const rows = page.locator('.trades-table .el-table__row')
  await expect(rows).toHaveCount(2)
  await expect(rows.filter({ hasText: 'VOIDED' }).getByRole('button', { name: '编辑', exact: true })).toBeDisabled()
  const edit = rows.filter({ hasText: 'ACTIVE' }).getByRole('button', { name: '编辑', exact: true })
  await edit.click()
  const dialog = page.getByRole('dialog', { name: '编辑交易流水' })
  await expect(dialog.getByRole('textbox', { name: '成交时间', exact: true })).toHaveValue(state.trade.executedAt)
  await expect(dialog.getByRole('textbox', { name: '成交数量', exact: true })).toHaveValue('100.1234')
  await expect(dialog.getByRole('textbox', { name: '成交价格', exact: true })).toHaveValue('10.123456')
  await expect(dialog.getByRole('textbox', { name: '其他费用', exact: true })).toHaveValue('0.4075')
  await expect(dialog).toContainText('归属和买卖方向不可修改')
  await expect(dialog.getByRole('button', { name: '保存修改', exact: true })).toBeInViewport()
  await expect(dialog.getByRole('button', { name: '取消', exact: true })).toBeInViewport()
  await page.screenshot({ path: 'test-results/trade-edit-dialog.png', fullPage: true, animations: 'disabled' })
  await dialog.getByRole('textbox', { name: '备注', exact: true }).fill('取消更改')
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  expect(state.edits).toHaveLength(0)
  await edit.click()
  await expect(dialog.getByRole('textbox', { name: '备注', exact: true })).toHaveValue('原始备注')
  await dialog.getByRole('textbox', { name: '备注', exact: true }).fill('只改备注')
  await dialog.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect.poll(() => state.edits.length).toBe(1)
  expect(state.edits[0]).toEqual({ revision: 7, memo: '只改备注' })
  await expect(page.getByText('交易已保存，对应持仓需要刷新', { exact: true })).toBeVisible()
  expect(state.trade.executedAt).toBe('2026-11-17T01:30:45.123456Z')
})

test('SQLite无时区UTC时间可编辑备注且不改写原始时间', async ({ page }) => {
  const { state } = await mockTrading(page)
  state.trade.executedAt = '2026-11-17T01:30:45.123456'
  await page.goto('/trades')
  await page.locator('.trades-table .el-table__row').filter({ hasText: 'ACTIVE' }).getByRole('button', { name: '编辑', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑交易流水' })
  await expect(dialog.getByRole('textbox', { name: '成交时间', exact: true })).toHaveValue(/01:30:45\.123456/)
  await dialog.getByRole('textbox', { name: '备注', exact: true }).fill('兼容旧时间响应')
  await dialog.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect.poll(() => state.edits.length).toBe(1)
  expect(state.edits[0]).toEqual({ revision: 7, memo: '兼容旧时间响应' })
  expect(state.trade.executedAt).toBe('2026-11-17T01:30:45.123456')
})

test('编辑校验与版本冲突保留输入，成功后的列表失败独立提示', async ({ page }) => {
  const { state } = await mockTrading(page)
  state.conflict = true
  await page.goto('/trades')
  await page.locator('.trades-table .el-table__row').filter({ hasText: 'ACTIVE' }).getByRole('button', { name: '编辑', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑交易流水' })
  await dialog.getByRole('textbox', { name: '成交数量', exact: true }).fill('-1')
  await dialog.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(dialog).toContainText('成交数量和价格必须为正数')
  expect(state.edits).toHaveLength(0)
  await dialog.getByRole('textbox', { name: '成交数量', exact: true }).fill('200.0000')
  await dialog.getByRole('textbox', { name: '印花税', exact: true }).fill('0.5678')
  await dialog.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(dialog).toContainText('交易已由其他操作修改')
  await expect(dialog.getByRole('textbox', { name: '成交数量', exact: true })).toHaveValue('200.0000')
  expect(state.dirty).toBe(true)
  state.conflict = false
  state.listFailsAfterSave = true
  await dialog.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(page.getByText('交易已保存，流水列表读取失败，请重新加载页面', { exact: true })).toBeVisible()
  expect(state.edits[1]).toEqual({ revision: 7, qty: '200.0000', stampDuty: '0.5678' })
})

test('活动仓位持久提示复用重算，行情刷新和取消均不清标记', async ({ page }) => {
  const { state } = await mockTrading(page)
  await page.goto('/dashboard')
  const row = page.locator('.ap-list .ap-row')
  await expect(row).toContainText('需要刷新')
  await page.screenshot({ path: 'test-results/trade-refresh-dashboard.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '刷新行情', exact: true }).click()
  await expect(row).toContainText('需要刷新')
  expect(state.recalculations).toHaveLength(0)
  await page.locator('.quick-trade .el-select').click()
  await page.getByRole('option', { name: '另一个账户 (CNY)' }).click()
  await row.getByRole('button', { name: '刷新持仓', exact: true }).dispatchEvent('click')
  await row.getByRole('button', { name: '刷新持仓', exact: true }).dispatchEvent('click')
  await expect(page.getByRole('dialog', { name: '重新计算持仓' })).toHaveCount(1)
  await page.getByRole('button', { name: '取消', exact: true }).click()
  expect(state.recalculations).toHaveLength(0)
  await page.reload()
  await expect(row).toContainText('需要刷新')
  await row.getByRole('button', { name: '刷新持仓', exact: true }).click()
  await page.getByRole('button', { name: '确认重新计算', exact: true }).click()
  await expect.poll(() => state.recalculations.length).toBe(1)
  expect(state.recalculations[0]!.searchParams.get('accountId')).toBe(accountId)
  expect(state.recalculations[0]!.searchParams.get('strategyId')).toBe(strategyId)
  await expect(row).not.toContainText('需要刷新')
  await expect(row.locator('.ap-sec-holding')).toContainText('200股')
})

test('无活动仓位仍有刷新入口，详情导航保留快照账户和策略', async ({ page }) => {
  const { state } = await mockTrading(page)
  state.noActiveRows = true
  await page.goto('/dashboard')
  await expect(page.locator('.ap-pending-scope')).toContainText('当前无活动仓位 · 需要刷新')
  await page.locator('.ap-pending-scope').getByRole('button', { name: '刷新持仓', exact: true }).click()
  await page.getByRole('button', { name: '确认重新计算', exact: true }).click()
  await expect(page.locator('.ap-pending-scope')).toHaveCount(0)
  state.noActiveRows = false
  state.dirty = true
  await page.reload()
  await page.locator('.ap-list .ap-code-link').click()
  await page.locator('.workspace-detail').getByRole('button', { name: '独立页打开' }).click()
  await expect(page).toHaveURL(new RegExp(`accountId=${accountId}.*strategyId=${strategyId}`))
  await expect.poll(() => state.lotsRequests.some((url) => url.searchParams.get('accountId') === accountId && url.searchParams.get('strategyId') === strategyId)).toBe(true)
  await expect(page.getByRole('button', { name: '刷新持仓', exact: true })).toBeVisible()
})

test('重算后的读取期间切换已结清条件，丢弃旧读取并立即按新条件重读', async ({ page }) => {
  const { state, snapshot } = await mockTrading(page)
  let held: Route | null = null
  let holdNextRead = false
  let heldBody: DashboardRead | null = null
  await page.route('**/api/v1/dashboard?**', async (route) => {
    if (holdNextRead && state.recalculations.length) {
      holdNextRead = false
      held = route
      heldBody = snapshot()
      state.dashboardRequests.push(new URL(route.request().url()))
      return
    }
    await route.fallback()
  })
  await page.goto('/dashboard')
  await page.locator('.ap-list .ap-row').getByRole('button', { name: '刷新持仓', exact: true }).click()
  holdNextRead = true
  await page.getByRole('button', { name: '确认重新计算', exact: true }).click()
  await expect.poll(() => held !== null).toBe(true)
  await page.getByText('显示已结清', { exact: true }).click()
  await expect(page.getByRole('switch')).toBeChecked()
  await respond(held!, heldBody)
  await expect.poll(() => state.dashboardRequests.some((url) => url.searchParams.get('includeClosed') === 'true')).toBe(true)
  await expect(page.getByText('已重建 1 个批次、0 条配对', { exact: true })).toBeVisible()
  await expect(page.getByText(/重算已完成，页面数据读取失败/)).toHaveCount(0)
})

test('详情无批次仍可刷新，失败保留提示并能更正后重试', async ({ page }) => {
  const { state } = await mockTrading(page)
  state.noActiveRows = true
  state.recalculateFails = true
  await page.goto(`/security/SH/600519?accountId=${accountId}&strategyId=${strategyId}`)
  const refresh = page.getByRole('button', { name: '刷新持仓', exact: true })
  await expect(refresh).toBeVisible()
  await page.screenshot({ path: 'test-results/trade-refresh-detail.png', fullPage: true, animations: 'disabled' })
  await refresh.click()
  await page.getByRole('button', { name: '确认重新计算', exact: true }).click()
  await expect(page.getByText(/重新计算失败：历史卖出数量超过买入/)).toBeVisible()
  await expect(refresh).toBeVisible()
  expect(state.dirty).toBe(true)
  state.recalculateFails = false
  await refresh.click()
  await page.getByRole('button', { name: '确认重新计算', exact: true }).click()
  await expect(refresh).toHaveCount(0)
  expect(state.recalculations).toHaveLength(2)
})

test('重算提交成功但读取失败时明确区分，重进按服务端状态展示', async ({ page }) => {
  const { state } = await mockTrading(page)
  await page.goto(`/security/SH/600519?accountId=${accountId}&strategyId=${strategyId}`)
  await page.getByRole('button', { name: '刷新持仓', exact: true }).click()
  state.lotsFail = true
  await page.getByRole('button', { name: '确认重新计算', exact: true }).click()
  await expect(page.getByText(/重算已完成，页面数据读取失败/)).toBeVisible()
  expect(state.dirty).toBe(false)
  await expect(page.getByText(/重新计算失败：/)).toHaveCount(0)
  state.lotsFail = false
  await page.reload()
  await expect(page.locator('.lot-table .el-table__row')).toHaveCount(1)
  await expect(page.getByRole('button', { name: '刷新持仓', exact: true })).toHaveCount(0)
})

test('确认期间离页不会向旧范围发送重算', async ({ page }) => {
  const { state } = await mockTrading(page)
  await page.goto(`/security/SH/600519?accountId=${accountId}&strategyId=${strategyId}`)
  await page.getByRole('button', { name: '刷新持仓', exact: true }).click()
  await page.getByText('交易流水 Trades').dispatchEvent('click')
  await expect(page.getByRole('heading', { name: '交易流水', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '确认重新计算', exact: true }).click()
  expect(state.recalculations).toHaveLength(0)
})

test('写前迟到批次响应不能覆盖刷新后的数量和标记', async ({ page }) => {
  await page.clock.install()
  const { state, lotsResponse } = await mockTrading(page)
  await page.goto(`/security/SH/600519?accountId=${accountId}&strategyId=${strategyId}`)
  await expect(page.getByRole('button', { name: '刷新持仓', exact: true })).toBeVisible()
  await expect.poll(() => state.lotsRequests.length).toBeGreaterThanOrEqual(2)
  state.holdNextLots = true
  await page.clock.runFor(3100)
  await expect.poll(() => state.heldLots !== null).toBe(true)
  await page.getByRole('button', { name: '刷新持仓', exact: true }).click()
  await page.getByRole('button', { name: '确认重新计算', exact: true }).click()
  await expect(page.getByRole('button', { name: '刷新持仓', exact: true })).toHaveCount(0)
  const held = state.heldLots!
  await respond(held, lotsResponse(new URL(held.request().url()), true, '100.1234'))
  await expect(page.locator('.lot-table .el-table__row').locator('td').nth(2)).toHaveText('200')
  await expect(page.getByRole('button', { name: '刷新持仓', exact: true })).toHaveCount(0)
})

test('重算期间切换账户不应用旧范围结果，完成后立即读取新账户', async ({ page }) => {
  const { state } = await mockTrading(page)
  let held: Route | null = null
  await page.route('**/api/v1/securities/*/recalculate?**', async (route) => { held = route })
  await page.goto(`/security/SH/600519?accountId=${accountId}&strategyId=${strategyId}`)
  await page.getByRole('button', { name: '刷新持仓', exact: true }).click()
  await page.getByRole('button', { name: '确认重新计算', exact: true }).click()
  await expect.poll(() => held !== null).toBe(true)
  await page.locator('.detail-head .el-select').click()
  await page.getByRole('option', { name: '另一个账户', exact: true }).click()
  state.dirty = false
  await respond(held!, { rebuiltLots: 1, rebuiltMatches: 0, needsRefresh: false })
  await expect.poll(() => state.lotsRequests.some((url) => url.searchParams.get('accountId') === 'account-secondary')).toBe(true)
  await expect(page.locator('.lot-table .el-table__row')).toHaveCount(1)
  await expect(page.getByRole('button', { name: '刷新持仓', exact: true })).toHaveCount(0)
  await expect(page.getByText('已重建 1 个批次、0 条配对', { exact: true })).toHaveCount(0)
})
