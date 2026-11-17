import { expect, test, type Page, type Route } from '@playwright/test'
import type { Lot, SettledTradeDetail } from '../src/api/types'

const securities = [
  { id: 'security-a', market: 'SH', symbol: '600519', name: '贵州茅台', currency: 'CNY' },
  { id: 'security-b', market: 'SZ', symbol: '000001', name: '平安银行', currency: 'CNY' },
  { id: 'security-us', market: 'US', symbol: 'AAPL', name: '苹果', currency: 'USD' },
]

/** 用两个闭仓批次和跨批次卖出提供完整事实明细，故意打乱输入顺序。 */
function settledDetails(): SettledTradeDetail[] {
  return [
    { lotId: 'aaaaaaaa-closed-lot-full', tradeId: 'buy-a', side: 'BUY', executedAt: '2026-11-10T01:30:00Z', qty: '100.0000', originalTradeQty: '100.0000', price: '10.123000', cashAmount: '1015.0000', realizedPnl: null },
    { lotId: 'aaaaaaaa-closed-lot-full', tradeId: 'sell-cross', side: 'SELL', executedAt: '2026-11-13T02:00:00Z', qty: '60.0000', originalTradeQty: '110.0000', price: '11.250000', cashAmount: '674.0000', realizedPnl: '65.0000' },
    { lotId: 'bbbbbbbb-closed-lot-full', tradeId: 'buy-b', side: 'BUY', executedAt: '2026-11-11T01:30:00Z', qty: '50.0000', originalTradeQty: '50.0000', price: '9.876000', cashAmount: '495.0000', realizedPnl: null },
    { lotId: 'aaaaaaaa-closed-lot-full', tradeId: 'sell-first', side: 'SELL', executedAt: '2026-11-12T02:00:00Z', qty: '40.0000', originalTradeQty: '40.0000', price: '12.345000', cashAmount: '492.0000', realizedPnl: '86.0000' },
    { lotId: 'bbbbbbbb-closed-lot-full', tradeId: 'sell-cross', side: 'SELL', executedAt: '2026-11-13T02:00:00Z', qty: '50.0000', originalTradeQty: '110.0000', price: '11.250000', cashAmount: '561.5000', realizedPnl: '66.5000' },
  ]
}

/** 复用既有批次表所需的最小合法批次，账务值始终来自模拟服务端。 */
function lot(id: string, status = 'CLOSED'): Lot {
  return { id, buyTradeId: `buy-${id}`, accountId: 'account-a', securityId: 'security-a', strategyId: 'default', cycleId: 'cycle-a', originalQty: '100.0000', originalCost: '1015.0000', remainingQty: status === 'OPEN' ? '100.0000' : '0.0000', remainingCost: status === 'OPEN' ? '1015.0000' : '0.0000', unitCost: '10.150000', executedAt: '2026-11-10T01:30:00Z', status, targets: [] }
}

/** 统一返回固定 JSON，阻止用例访问真实账本或上游行情。 */
async function respond(route: Route, body: unknown) {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

/** 为账户和路由隔离提供可延迟的 lots 响应，不产生逐批额外请求。 */
async function mockSettledPage(page: Page) {
  let releaseAccountA = () => {}
  const accountAGate = new Promise<void>((resolve) => { releaseAccountA = resolve })
  const state = { holdAccountA: false, heldAccountA: 0, lotsRequests: [] as Array<{ securityId: string; accountId: string; includeClosed: boolean }>, extraRequests: [] as string[], releaseAccountA }
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    if (path === '/accounts') return respond(route, [{ id: 'account-a', name: '主账户', currency: 'CNY' }, { id: 'account-b', name: '第二账户', currency: 'CNY' }])
    if (path === '/securities') return respond(route, securities)
    if (path.endsWith('/intraday')) return respond(route, { symbol: 'SH:600519', currency: 'CNY', tradingDate: '2026-11-17', points: [{ time: '2026-11-17T01:30:00Z', price: '10.123000' }, { time: '2026-11-17T02:00:00Z', price: '10.250000' }], quote: null, provider: 'fixture', fetchedAt: '2026-11-17T02:00:00Z', isStale: false, error: null })
    if (path.endsWith('/lots')) {
      const securityId = path.split('/')[2]!
      const accountId = url.searchParams.get('accountId') ?? ''
      const includeClosed = url.searchParams.get('includeClosed') === 'true'
      state.lotsRequests.push({ securityId, accountId, includeClosed })
      if (accountId === 'account-a' && state.holdAccountA) { state.heldAccountA += 1; await accountAGate }
      const mainScope = securityId === 'security-a' && accountId === 'account-a'
      const otherDetail: SettledTradeDetail = { lotId: securityId === 'security-b' ? 'cccccccc-other-security' : 'dddddddd-other-account', tradeId: 'buy-other', side: 'BUY', executedAt: '2026-11-15T01:30:00Z', qty: '25.0000', originalTradeQty: '25.0000', price: '8.765000', cashAmount: '220.0000', realizedPnl: null }
      const closed = mainScope ? [
        { ...lot('aaaaaaaa-closed-lot-full'), buyTradeId: 'buy-a' },
        { ...lot('bbbbbbbb-closed-lot-full'), buyTradeId: 'buy-b', originalQty: '50.0000', originalCost: '495.0000', unitCost: '9.900000', executedAt: '2026-11-11T01:30:00Z' },
      ] : [{ ...lot(otherDetail.lotId), buyTradeId: otherDetail.tradeId, accountId, securityId, originalQty: otherDetail.qty, originalCost: otherDetail.cashAmount, unitCost: '8.800000', executedAt: otherDetail.executedAt }]
      return respond(route, { lots: includeClosed ? [lot('eeeeeeee-open-lot', 'OPEN'), ...closed] : [lot('eeeeeeee-open-lot', 'OPEN')], closedLotCount: closed.length, realizedPnl: '217.5000', quote: null, currency: 'CNY', closedTradeDetails: includeClosed ? mainScope ? settledDetails() : [otherDetail] : [] })
    }
    state.extraRequests.push(path)
    return respond(route, [])
  })
  return state
}

test('显示已结清即完整展示所有批次买卖，按时间倒序且使用本批分摊值', async ({ page }) => {
  const state = await mockSettledPage(page)
  await page.goto('/security/SH/600519')
  await expect(page.locator('.lot-table .el-table__row')).toHaveCount(1)
  await expect(page.getByRole('heading', { name: '已结清买卖明细' })).toHaveCount(0)
  await page.getByText('显示已结清', { exact: true }).click()
  const table = page.locator('.settled-trade-table')
  const rows = table.locator('.el-table__row')
  await expect(rows).toHaveCount(5)
  for (let index = 0; index < 5; index += 1) await expect(rows.nth(index)).toBeVisible()
  await expect(rows.locator('.lot-code')).toHaveText(['bbbbbbbb', 'aaaaaaaa', 'aaaaaaaa', 'bbbbbbbb', 'aaaaaaaa'])
  await expect(rows.first().locator('.lot-code')).toHaveAttribute('title', 'bbbbbbbb-closed-lot-full')
  await expect(rows.first().locator('td').nth(4)).toContainText('整笔成交 110 股')
  await expect(rows.first().locator('td').nth(4).locator('strong')).toHaveText('50')
  await expect(rows.first().locator('td').nth(5)).toContainText('¥ 561.50')
  await expect(rows.first().locator('td').nth(6)).toHaveText('¥ 66.50')
  await expect(rows.nth(1).locator('td').nth(4).locator('strong')).toHaveText('60')
  await expect(rows.nth(1).locator('td').nth(5)).toContainText('¥ 674.00')
  await expect(rows.nth(1).locator('td').nth(6)).toHaveText('¥ 65.00')
  await expect(rows.nth(2).locator('td').nth(3)).toHaveText('¥ 12.345')
  await expect(rows.nth(2).locator('td').nth(4).locator('strong')).toHaveText('40')
  await expect(rows.last().locator('td').nth(3)).toHaveText('¥ 10.123')
  await expect(rows.last().locator('td').nth(5)).toContainText('¥ 1,015.00')
  await expect(rows.last().locator('td').nth(6)).toHaveText('—')
  await expect(rows.filter({ hasText: '买入' })).toHaveCount(2)
  await expect(rows.filter({ hasText: '卖出' })).toHaveCount(3)
  const summary = page.locator('.lot-table .el-table__row').filter({ hasText: 'bbbbbbbb' })
  await expect(summary.locator('td').nth(2)).toHaveText('50')
  await expect(summary.locator('td').nth(4)).toHaveText('¥ 9.900')
  // 新增行的 Element Plus 标签有入场缩放，等待真实文字框稳定后再采集视觉证据。
  await expect.poll(() => table.locator('.el-tag').evaluateAll((tags) => tags.every((tag) => {
    const text = tag.querySelector('.el-tag__content')
    return tag.getBoundingClientRect().width >= 36 && (text?.getBoundingClientRect().width ?? 0) >= 20 && Number(getComputedStyle(tag).opacity) >= 0.95
  }))).toBe(true)
  await page.screenshot({ path: 'test-results/portfolio-detail-settled.png', fullPage: true, animations: 'disabled' })
  await page.getByText('显示已结清', { exact: true }).click()
  await expect(table).toHaveCount(0)
  await expect(page.locator('.lot-table .el-table__row')).toHaveCount(1)
  await expect(page.getByRole('img', { name: '当前交易日股价走势图' })).toBeVisible()
  expect(state.extraRequests).toEqual([])
})

test('账户切换立即清除旧明细，旧账户迟到响应不能恢复', async ({ page }) => {
  const state = await mockSettledPage(page)
  await page.goto('/security/SH/600519')
  await page.getByText('显示已结清', { exact: true }).click()
  const table = page.locator('.settled-trade-table')
  await expect(table.locator('.el-table__row')).toHaveCount(5)
  await expect(page.getByRole('img', { name: '当前交易日股价走势图' })).toBeVisible()
  state.holdAccountA = true
  try {
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
    await expect.poll(() => state.heldAccountA).toBeGreaterThan(0)
    await page.locator('.detail-head .el-select').click()
    await page.getByRole('option', { name: '第二账户' }).click()
    await expect(table.locator('.el-table__row')).toHaveCount(1)
    await expect(table).toContainText('dddddddd')
    await expect(table).not.toContainText('aaaaaaaa')
    const delayedResponse = page.waitForResponse((response) => response.url().includes('/lots?') && new URL(response.url()).searchParams.get('accountId') === 'account-a')
    state.releaseAccountA()
    await delayedResponse
    await expect(table.locator('.el-table__row')).toHaveCount(1)
    await expect(table).toContainText('dddddddd')
    await expect(table).not.toContainText('aaaaaaaa')
  } finally { state.releaseAccountA() }
})

test('切换证券或没有同币种账户时不残留上一证券已结清明细', async ({ page }) => {
  await mockSettledPage(page)
  await page.goto('/security/SH/600519')
  await page.getByText('显示已结清', { exact: true }).click()
  const table = page.locator('.settled-trade-table')
  await expect(table.locator('.el-table__row')).toHaveCount(5)
  await page.evaluate(() => { window.history.pushState({}, '', '/security/SZ/000001'); window.dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.getByRole('heading', { name: 'SZ:000001 平安银行' })).toBeVisible()
  await expect(table.locator('.el-table__row')).toHaveCount(1)
  await expect(table).toContainText('cccccccc')
  await expect(table).not.toContainText('aaaaaaaa')
  await page.evaluate(() => { window.history.pushState({}, '', '/security/US/AAPL'); window.dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.getByText(/暂无 USD 账户/)).toBeVisible()
  await expect(table.locator('.el-table__row')).toHaveCount(0)
  await expect(table).toContainText('暂无已结清买卖明细')
  await expect(table).not.toContainText('cccccccc')
})
