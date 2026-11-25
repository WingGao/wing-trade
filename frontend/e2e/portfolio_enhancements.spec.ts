import { expect, test, type Page, type Route } from '@playwright/test'
import type { DashboardRead, IntradayRead, Lot, LotsSummary, ProfitSetting, ProfitTarget } from '../src/api/types'

const security = { id: 'security-1', market: 'SH', symbol: '600519', name: '贵州茅台', currency: 'CNY' as const }
const quote = { symbol: 'SH:600519', securityId: security.id, currency: 'CNY' as const, lastPrice: '10.123000', changePct: '0', sourceTime: '2026-11-17T02:00:00Z', fetchedAt: '2026-11-17T02:00:00Z', provider: 'fixture', isStale: false }
const detailSummary: LotsSummary = { remainingQty: '100.0000', remainingCost: '1000.0000', realizedPnl: '52.0000', unrealizedPnl: '12.3000' }
const emptySummary: LotsSummary = { remainingQty: '0.0000', remainingCost: '0.0000', realizedPnl: '0.0000', unrealizedPnl: null }
const noOpenSummary: LotsSummary = { remainingQty: '0.0000', remainingCost: '0.0000', realizedPnl: '52.0000', unrealizedPnl: null }

/** 用固定响应模拟 API，测试不依赖真实行情网络。 */
async function respond(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: status === 204 ? undefined : JSON.stringify(body) })
}

/** 提供新的完整市值快照，便于同时核对三位股价与整数数量。 */
function dashboard(): DashboardRead {
  return {
    accountId: 'account-cny', currency: 'CNY', asOf: quote.fetchedAt, marketSessionOpen: true, pollAfterSeconds: 60,
    summary: { currency: 'CNY', marketValue: '1012.3000', totalBuyCashOut: '1000', totalSellCashIn: '0', realizedPnl: '0', unrealizedPnl: '12.3', unrealizedReturnPct: '1.23', remainingQty: '100.0000', remainingCost: '1000', valuationStatus: 'fresh', totalOpenPositions: 1, quotedOpenPositions: 1, staleOpenPositions: 0 },
    positions: [{ securityId: security.id, strategyId: 'default', market: security.market, symbol: security.symbol, name: security.name, currency: 'CNY', remainingQty: '100.0000', remainingCost: '1000', averageCost: '10', realizedPnl: '0', unrealizedPnl: '12.3', unrealizedReturnPct: '1.23', quote, lowestPriceGroup: null }], quotes: [quote],
  }
}

/** 构造详情页的买入批次，状态与估值可按场景覆盖。 */
function makeLot(overrides: Partial<Lot> = {}): Lot {
  return { id: 'bbbbbbbb-newer-lot-full', buyTradeId: 'buy-1', accountId: 'account-cny', securityId: security.id, strategyId: 'default', cycleId: 'cycle-1', originalQty: '100.0000', originalCost: '1000', remainingQty: '100.0000', remainingCost: '1000', unitCost: '10', executedAt: '2026-11-17T01:45:00Z', status: 'OPEN', unrealizedPnl: '12.3000', unrealizedReturnPct: '1.230000', targets: [], ...overrides }
}

/** 模拟分级止盈和详情刷新，保存写请求以验证继承读取零写入。 */
async function mockDetail(page: Page) {
  const state = { intradayRequests: 0, intradayError: false, lotsError: false, lotsQuoteMissing: false, lotsQuoteStale: false, noOpenLots: false, activeIntraday: 0, maxIntraday: 0, lotRequests: 0, recalculateRequests: 0, targetPosts: [] as Array<Record<string, string>>, settingsWrites: [] as Array<{ scope: string; payload: Record<string, unknown> }>, targets: [] as ProfitTarget[], global: { targetReturnPct: '0.1', calculationBasis: 'NET', alertEnabled: true } as ProfitSetting | null, specific: null as ProfitSetting | null }
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    const method = route.request().method()
    if (path === '/auth/me') return respond(route, { user: { id: 'admin-1', email: 'admin@localhost', displayName: '默认管理员', role: 'ADMIN', status: 'ACTIVE', isDefaultAdmin: true, createdAt: '2026-11-18T00:00:00Z' } })
    if (path === '/accounts') return respond(route, [{ id: 'account-cny', name: 'A股账户', currency: 'CNY' }, { id: 'account-usd', name: '美股账户', currency: 'USD' }])
    if (path === '/securities') return respond(route, [security])
    if (path === '/trades') return respond(route, [])
    if (path.endsWith('/intraday')) {
      state.intradayRequests += 1
      if (state.intradayError) return respond(route, { error: { message: '行情源超时' } }, 503)
      state.activeIntraday += 1
      state.maxIntraday = Math.max(state.maxIntraday, state.activeIntraday)
      const result: IntradayRead = { symbol: quote.symbol, currency: 'CNY', tradingDate: '2026-11-17', points: [{ time: '2026-11-17T01:30:00Z', price: '10' }, { time: '2026-11-17T02:00:00Z', price: quote.lastPrice }], quote, provider: 'fixture', fetchedAt: quote.fetchedAt, isStale: false, error: null }
      await respond(route, result)
      state.activeIntraday -= 1
      return
    }
    if (path.endsWith('/lots')) {
      state.lotRequests += 1
      if (state.lotsError) return respond(route, { error: { message: '批次读取失败' } }, 503)
      expect(url.searchParams.get('accountId')).toBe('account-cny')
      const setting = state.specific ?? state.global
      const explicit = state.targets.find((target) => target.status === 'ACTIVE' || target.status === 'TRIGGERED')
      const effective = explicit ? { ...explicit, source: 'LOT' } : setting ? { ...setting, source: state.specific ? 'SECURITY' : 'GLOBAL', targetPriceEstimate: '11.000000' } : null
      const open = makeLot({ targets: state.targets, effectiveTarget: effective as Lot['effectiveTarget'] })
      const closed = makeLot({ id: 'aaaaaaaa-older-lot-full', status: 'CLOSED', executedAt: '2026-11-16T01:30:00Z', remainingQty: '0.0000', unrealizedPnl: '999', unrealizedReturnPct: '99', effectiveTarget: null })
      const lotsQuote = state.lotsQuoteMissing ? null : state.lotsQuoteStale ? { ...quote, isStale: true } : quote
      const summary = state.noOpenLots ? noOpenSummary : state.lotsQuoteMissing ? { ...detailSummary, unrealizedPnl: null } : detailSummary
      const returnedLots = state.noOpenLots ? (url.searchParams.get('includeClosed') === 'true' ? [closed] : []) : (url.searchParams.get('includeClosed') === 'true' ? [closed, open] : [open])
      return respond(route, { lots: returnedLots, closedLotCount: 1, realizedPnl: '52.0000', summary, quote: lotsQuote, currency: 'CNY' })
    }
    if (path.endsWith('/recalculate')) { state.recalculateRequests += 1; return respond(route, { rebuiltLots: 2, rebuiltMatches: 1 }) }
    if (path.endsWith('/profit-settings')) {
      const scope = path === '/profit-settings' ? 'global' : 'specific'
      if (method === 'PUT') {
        const payload = route.request().postDataJSON() as ProfitSetting
        state.settingsWrites.push({ scope, payload: { ...payload } })
        state[scope] = payload.targetReturnPct === null ? null : payload
      }
      return respond(route, state[scope])
    }
    if (path.startsWith('/lots/') && path.endsWith('/targets')) {
      const payload = route.request().postDataJSON() as Record<string, string>
      state.targetPosts.push(payload)
      const target: ProfitTarget = { id: 'target-1', lotId: 'bbbbbbbb-newer-lot-full', plannedSellQty: payload.plannedSellQty!, targetReturnPct: payload.targetReturnPct!, calculationBasis: 'NET', alertEnabled: true, targetPriceEstimate: '12', status: 'ACTIVE' }
      state.targets = [target]
      return respond(route, target, 201)
    }
    if (path.startsWith('/targets/') && method === 'DELETE') { state.targets = []; return respond(route, null, 204) }
    return respond(route, [])
  })
  return state
}

test('看板展示完整市值并通过外部查询取得证券名称', async ({ page }) => {
  let lookupCalls = 0
  let createdName = ''
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    if (path === '/auth/me') return respond(route, { user: { id: 'admin-1', email: 'admin@localhost', displayName: '默认管理员', role: 'ADMIN', status: 'ACTIVE', isDefaultAdmin: true, createdAt: '2026-11-18T00:00:00Z' } })
    if (path === '/dashboard') return respond(route, dashboard())
    if (path === '/accounts') return respond(route, [{ id: 'account-cny', name: 'A股账户', currency: 'CNY' }])
    if (path === '/securities/lookup') { lookupCalls += 1; return respond(route, { securities: [{ market: 'SH', symbol: '600519', name: '外部名称贵州茅台', currency: 'CNY' }], error: null }) }
    if (path === '/securities' && route.request().method() === 'POST') { createdName = route.request().postDataJSON().name; return respond(route, { ...security, name: createdName }, 201) }
    if (path === '/securities') return respond(route, [])
    if (path.endsWith('/lots')) return respond(route, { lots: [], closedLotCount: 0, realizedPnl: '0', summary: emptySummary })
    return respond(route, null)
  })
  await page.goto('/dashboard')
  await expect(page.locator('.metric-card').filter({ hasText: '持仓总价值' })).toContainText('¥ 1,012.30')
  await expect(page.locator('.ap-table-head')).toContainText('证券 / 持仓')
  await expect(page.locator('.ap-table-head')).toContainText('总市值')
  await expect(page.locator('.ap-table-head')).toContainText('上卖')
  const row = page.locator('.ap-list .ap-row')
  await expect(row.locator('.ap-mv')).toHaveText('¥1,012.30')
  await expect(row.locator('.ap-price-current')).toHaveText('10.123')
  await expect(row.locator('.ap-price-row').first()).toContainText('0.00%')
  await expect(row.locator('.ap-sec-holding')).toContainText('100股 · 均 ¥10.00')
  await expect(row.locator('.ap-price-row').nth(2)).toContainText('暂无卖出')
  await row.locator('.ap-price-row').first().hover()
  await expect(page.getByRole('tooltip')).toContainText('报价更新信息')
  await expect(page.getByRole('tooltip')).toContainText('fixture')
  await expect(page.getByRole('tooltip')).toContainText('报价时间：')
  await expect(page.getByRole('tooltip')).toContainText('获取时间：')
  await page.screenshot({ path: 'test-results/portfolio-dashboard.png', fullPage: true })
  await page.getByPlaceholder('输入证券代码、名称，例如 600519').fill('600519')
  await page.getByRole('button', { name: '查找证券' }).click()
  await expect.poll(() => lookupCalls).toBe(1)
  await page.getByRole('button', { name: /外部名称贵州茅台/ }).click()
  await expect(page.getByText('当前证券：')).toContainText('外部名称贵州茅台')
  expect(createdName).toBe('外部名称贵州茅台')
})

test('详情批次倒序、短编号、浮盈和只读继承正确展示', async ({ page }) => {
  const state = await mockDetail(page)
  await page.goto('/security/SH/600519')
  await expect(page.getByRole('img', { name: '当前交易日股价走势图' })).toBeVisible()
  const holdingSummary = page.locator('.holding-summary')
  await expect(holdingSummary).toContainText('总持股数')
  await expect(holdingSummary).toContainText('持仓成本')
  await expect(holdingSummary).toContainText('已实现收益')
  await expect(holdingSummary).toContainText('总浮盈')
  await expect(holdingSummary.locator('.summary-value').nth(0)).toHaveText('100')
  await expect(holdingSummary.locator('.summary-value').nth(1)).toHaveText('¥ 1,000.00')
  await expect(holdingSummary.locator('.summary-value').nth(2)).toHaveText('¥ 52.00')
  await expect(holdingSummary.locator('.summary-value').nth(3)).toHaveText('¥ 12.30')
  await expect(holdingSummary.locator('.summary-value').nth(2)).toHaveClass(/up/)
  await expect(holdingSummary.locator('.summary-value').nth(3)).toHaveClass(/up/)
  const summaryBeforeToggle = await holdingSummary.locator('.summary-value').allTextContents()
  await page.getByText('显示已结清').click()
  const rows = page.locator('.lot-table .el-table__row')
  await expect(rows).toHaveCount(2)
  await expect.poll(() => holdingSummary.locator('.summary-value').allTextContents()).toEqual(summaryBeforeToggle)
  await expect(rows.first().locator('.lot-code')).toHaveText('bbbbbbbb')
  await expect(rows.first().locator('.lot-code')).toHaveAttribute('title', 'bbbbbbbb-newer-lot-full')
  await expect(rows.first().locator('td').nth(2)).toHaveText('100')
  await expect(rows.first()).toContainText('¥ 12.30')
  await expect(rows.first()).toContainText('1.23%')
  await expect(rows.first()).toContainText('继承全局')
  await expect(rows.first()).toContainText('10.00%')
  await expect(rows.last().locator('td').nth(6)).toHaveText('—')
  expect(state.targetPosts).toHaveLength(0)
  expect(state.settingsWrites).toHaveLength(0)
  await page.screenshot({ path: 'test-results/portfolio-detail.png', fullPage: true })
})

test('全局、个股和批次设置提交精确小数率，清空批次恢复继承', async ({ page }) => {
  const state = await mockDetail(page)
  await page.goto('/security/SH/600519')
  await expect(page.locator('.lot-table .el-table__row')).toHaveCount(1)
  await page.getByRole('button', { name: '全局止盈设置', exact: true }).click()
  let dialog = page.getByRole('dialog', { name: '全局止盈设置', exact: true })
  await expect(dialog.locator('input').first()).toHaveValue('10')
  await dialog.locator('input').first().fill('10.123456789')
  await dialog.getByRole('button', { name: '保存设置' }).click()
  await expect.poll(() => state.global?.targetReturnPct).toBe('0.10123456789')
  await page.getByRole('button', { name: '个股止盈设置', exact: true }).click()
  dialog = page.getByRole('dialog', { name: '个股止盈设置', exact: true })
  await expect(dialog.locator('input').first()).toHaveValue('')
  await dialog.locator('input').first().fill('12.5')
  await dialog.getByRole('button', { name: '保存设置' }).click()
  await expect.poll(() => state.specific?.targetReturnPct).toBe('0.125')
  await expect(page.locator('.lot-table')).toContainText('继承个股')
  await page.locator('.lot-table').getByRole('button', { name: '设置', exact: true }).click()
  const batch = page.getByRole('dialog', { name: '设置批次止盈目标' })
  await expect(batch.getByPlaceholder('留空继承个股 / 全局')).toHaveValue('')
  await batch.getByPlaceholder('留空继承个股 / 全局').fill('20')
  await batch.getByRole('button', { name: '保存目标' }).click()
  await expect.poll(() => state.targetPosts.length).toBe(1)
  expect(state.targetPosts[0]).toMatchObject({ targetReturnPct: '0.2', plannedSellQty: '100' })
  await expect(page.locator('.lot-table')).toContainText('批次设置')
  await page.locator('.lot-table').getByRole('button', { name: '设置', exact: true }).click()
  await batch.getByRole('button', { name: '恢复继承' }).click()
  await expect(page.locator('.lot-table')).toContainText('继承个股')
  expect(state.targets).toHaveLength(0)
})

test('已完成分段不掩盖继承，冲突分段保留说明且不隐藏上级目标', async ({ page }) => {
  const state = await mockDetail(page)
  state.targets = [{ id: 'finished-target', lotId: 'bbbbbbbb-newer-lot-full', plannedSellQty: '100', targetReturnPct: '0.2', calculationBasis: 'NET', alertEnabled: true, targetPriceEstimate: '12', status: 'COMPLETED' }]
  await page.goto('/security/SH/600519')
  const table = page.locator('.lot-table')
  await expect(table).toContainText('继承全局')
  await expect(table).not.toContainText('批次设置')
  await table.getByRole('button', { name: '设置', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '设置批次止盈目标' })
  await expect(dialog.getByPlaceholder('留空继承个股 / 全局')).toHaveValue('')
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  state.targets[0]!.status = 'CONFLICT'
  await page.reload()
  await expect(table).toContainText('计划量冲突')
  await expect(table).toContainText('继承全局')
  expect(state.targetPosts).toHaveLength(0)
})

test('详情每三秒刷新，隐藏和离页停止，重建必须经过确认', async ({ page }) => {
  await page.clock.install()
  const state = await mockDetail(page)
  await page.goto('/security/SH/600519')
  await expect(page.getByRole('img', { name: '当前交易日股价走势图' })).toBeVisible()
  await expect.poll(() => state.intradayRequests).toBe(1)
  await page.clock.runFor(3100)
  await expect.poll(() => state.intradayRequests).toBe(2)
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')) })
  await page.clock.runFor(12000)
  expect(state.intradayRequests).toBe(2)
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange')) })
  await expect.poll(() => state.intradayRequests).toBe(3)
  expect(state.maxIntraday).toBe(1)
  await page.getByRole('button', { name: '重新计算', exact: true }).click()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  expect(state.recalculateRequests).toBe(0)
  await page.getByRole('button', { name: '重新计算', exact: true }).click()
  await page.getByRole('button', { name: '确认重新计算', exact: true }).click()
  await expect.poll(() => state.recalculateRequests).toBe(1)
  await page.getByText('交易流水 Trades').click()
  await expect(page.getByRole('heading', { name: '交易流水', exact: true })).toBeVisible()
  const before = state.intradayRequests
  await page.clock.runFor(12000)
  expect(state.intradayRequests).toBe(before)
})

test('交易证券显示代码名称，内部编号只保留提示，股数为整数', async ({ page }) => {
  await page.route('**/api/v1/auth/me', (route) => respond(route, { user: { id: 'admin-1', email: 'admin@localhost', displayName: '默认管理员', role: 'ADMIN', status: 'ACTIVE', isDefaultAdmin: true, createdAt: '2026-11-18T00:00:00Z' } }))
  await page.route('**/api/v1/securities**', (route) => respond(route, [security]))
  await page.route('**/api/v1/trades', (route) => respond(route, [{ id: 'trade-1', securityId: security.id, executedAt: '2026-11-17T02:00:00Z', side: 'BUY', qty: '100.0000', price: '10.123', feesTotal: '0', status: 'ACTIVE' }]))
  await page.goto('/trades')
  const row = page.locator('.trades-table .el-table__row')
  await expect(row).toContainText('SH:600519')
  await expect(row).toContainText('贵州茅台')
  await expect(row).not.toContainText('security-1')
  await expect(row.locator('[title]')).toHaveAttribute('title', '证券 ID：security-1')
  await expect(row.locator('td').nth(3)).toHaveText('100')
})

test('日内行情失败保留真实曲线并明确过期，交易入口仍可用', async ({ page }) => {
  await page.clock.install()
  const state = await mockDetail(page)
  await page.goto('/security/SH/600519')
  const chart = page.getByRole('img', { name: '当前交易日股价走势图' })
  await expect(chart).toBeVisible()
  state.intradayError = true
  await page.clock.runFor(3100)
  await expect(page.getByText(/行情刷新失败：行情源超时/)).toBeVisible()
  await expect(chart).toBeVisible()
  await expect(page.locator('.quote-value-row')).toContainText('已过期')
  await expect(page.locator('.summary-item').filter({ hasText: '总浮盈' })).toContainText('参考浮盈 · 行情已过期')
  await expect(page.getByRole('button', { name: '录入买入', exact: true })).toBeEnabled()
})

test('详情汇总无有效行情时保留空值并明确提示', async ({ page }) => {
  const state = await mockDetail(page)
  state.lotsQuoteMissing = true
  await page.goto('/security/SH/600519')
  const totalProfit = page.locator('.summary-item').filter({ hasText: '总浮盈' })
  await expect(totalProfit.locator('.summary-value')).toHaveText('—')
  await expect(totalProfit.locator('.summary-value')).toHaveClass(/neutral/)
  await expect(totalProfit).toContainText('暂无有效行情')
})

test('详情批次读取失败时清空旧汇总', async ({ page }) => {
  await page.clock.install()
  const state = await mockDetail(page)
  await page.goto('/security/SH/600519')
  const totalProfit = page.locator('.summary-item').filter({ hasText: '总浮盈' }).locator('.summary-value')
  await expect(totalProfit).toHaveText('¥ 12.30')
  state.lotsError = true
  await page.clock.runFor(3100)
  await expect(totalProfit).toHaveText('—')
  await expect(totalProfit).toHaveClass(/neutral/)
})

test('详情汇总保留服务端过期行情参考值并明确提示', async ({ page }) => {
  const state = await mockDetail(page)
  state.lotsQuoteStale = true
  await page.goto('/security/SH/600519')
  const totalProfit = page.locator('.summary-item').filter({ hasText: '总浮盈' })
  await expect(totalProfit.locator('.summary-value')).toHaveText('¥ 12.30')
  await expect(totalProfit).toContainText('参考浮盈 · 行情已过期')
})

test('详情无剩余持仓时总浮盈为空并区分空仓提示', async ({ page }) => {
  const state = await mockDetail(page)
  state.noOpenLots = true
  await page.goto('/security/SH/600519')
  const totalProfit = page.locator('.summary-item').filter({ hasText: '总浮盈' })
  await expect(totalProfit.locator('.summary-value')).toHaveText('—')
  await expect(totalProfit.locator('.summary-value')).toHaveClass(/neutral/)
  await expect(totalProfit).toContainText('暂无持仓')
  await expect(totalProfit).not.toContainText('暂无有效行情')
})

test('外部查名失败保留本地证券结果并明确提示', async ({ page }) => {
  let lookups = 0
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    if (path === '/accounts') return respond(route, [{ id: 'account-cny', name: 'A股账户', currency: 'CNY' }])
    if (path === '/auth/me') return respond(route, { user: { id: 'admin-1', email: 'admin@localhost', displayName: '默认管理员', role: 'ADMIN', status: 'ACTIVE', isDefaultAdmin: true, createdAt: '2026-11-18T00:00:00Z' } })
    if (path === '/dashboard') return respond(route, dashboard())
    if (path === '/securities/lookup') { lookups += 1; return respond(route, { securities: [], error: '外部接口暂不可用，本地结果可继续使用' }) }
    if (path === '/securities') return respond(route, [security])
    if (path.endsWith('/lots')) return respond(route, { lots: [], closedLotCount: 0, realizedPnl: '0', summary: emptySummary })
    return respond(route, null)
  })
  await page.goto('/dashboard')
  await page.getByPlaceholder('输入证券代码、名称，例如 600519').fill('600519')
  await page.getByRole('button', { name: '查找证券' }).click()
  await expect(page.getByText('外部接口暂不可用，本地结果可继续使用')).toBeVisible()
  await page.getByRole('button', { name: /SH:600519 贵州茅台/ }).click()
  await expect(page.getByRole('button', { name: '买入记账' })).toBeEnabled()
  expect(lookups).toBe(1)
})
