import { expect, test, type Page } from '@playwright/test'
import type { DashboardPosition, DashboardRead, DashboardSummary, Quote, SellPreview, Trade } from '../src/api/types'

const asOf = '2026-11-16T06:30:00Z'
const sourceTime = '2026-11-16T06:29:58Z'
const fetchedAt = '2026-11-16T06:30:00Z'

/** 构造测试行情，允许单项覆盖边界字段。 */
function makeQuote(overrides: Partial<Quote> = {}): Quote {
  return {
    symbol: 'SH:600519',
    securityId: 'security-1',
    lastPrice: '10.000000',
    prevClose: '9.500000',
    changePct: '5.263158',
    sourceTime,
    fetchedAt,
    provider: 'test-provider',
    isStale: false,
    market: 'SH',
    currency: 'CNY',
    ...overrides,
  }
}

/** 构造测试持仓，确保整份 DTO 与首页契约一致。 */
function makePosition(overrides: Partial<DashboardPosition> = {}): DashboardPosition {
  return {
    securityId: 'security-1',
    market: 'SH',
    symbol: '600519',
    name: '贵州茅台',
    currency: 'CNY',
    strategyId: 'default',
    remainingQty: '100.0000',
    remainingCost: '1000.0000',
    averageCost: '10.000000',
    realizedPnl: '0.0000',
    unrealizedPnl: '0.0000',
    unrealizedReturnPct: '0.000000',
    strategyBreakevenPrice: '10.000000',
    quote: makeQuote(),
    lowestPriceGroup: {
      purchasePrice: '8.000000',
      lotCount: 2,
      remainingQty: '150.0000',
      remainingCost: '1200.0000',
      unrealizedPnl: '300.0000',
      unrealizedReturnPct: '25.000000',
    },
    ...overrides,
  }
}

/** 构造测试账户汇总，默认表示行情完整。 */
function makeSummary(overrides: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    currency: 'CNY',
    marketValue: '1000.0000',
    totalBuyCashOut: '1000.0000',
    totalSellCashIn: '0.0000',
    realizedPnl: '0.0000',
    unrealizedPnl: '0.0000',
    unrealizedReturnPct: '0.000000',
    remainingQty: '100.0000',
    remainingCost: '1000.0000',
    valuationStatus: 'fresh',
    totalOpenPositions: 1,
    quotedOpenPositions: 1,
    staleOpenPositions: 0,
    ...overrides,
  }
}

/** 构造完整首页响应，测试可按场景覆盖持仓、汇总或调度间隔。 */
function makeDashboard(overrides: Partial<DashboardRead> = {}): DashboardRead {
  return {
    accountId: 'account-cny',
    currency: 'CNY',
    asOf,
    marketSessionOpen: true,
    pollAfterSeconds: 60,
    positions: [makePosition()],
    summary: makeSummary(),
    quotes: [makeQuote()],
    ...overrides,
  }
}

/** 返回交易接口的最小成功载荷。 */
function makeTrade(): Trade {
  return {
    id: 'trade-1',
    accountId: 'account-cny',
    securityId: 'security-1',
    strategyId: 'default',
    side: 'BUY',
    executedAt: '2026-11-16T06:30:00Z',
    qty: '100.0000',
    price: '10.000000',
    grossAmount: '1000.0000',
    feesTotal: '0.0000',
    status: 'ACTIVE',
  }
}

/** 构造包含真实买入字段的卖出预览响应。 */
function makeSellPreview(qty: string): SellPreview {
  return {
    currency: 'CNY',
    executedAt: '2026-11-05T02:00:00.000Z',
    qty: `${qty}.0000`,
    price: '30.000000',
    grossAmount: `${Number(qty) * 30}.0000`,
    feesTotal: '0.0000',
    cashIn: `${Number(qty) * 30}.0000`,
    realizedPnl: '52.0000',
    matches: [
      {
        lotId: 'lot-newer-1234567890',
        buyTradeId: 'trade-newer-1234567890',
        buyExecutedAt: '2026-11-02T01:00:00.000Z',
        buyPrice: '20.000000',
        buyQty: '10.0000',
        availableQty: '10.0000',
        matchedQty: `${qty}.0000`,
        allocatedBuyCost: '82.0000',
        allocatedSellNet: `${Number(qty) * 30}.0000`,
        realizedPnl: '52.0000',
        matchingMethod: 'EQUAL',
      },
    ],
  }
}

/** 模拟账户列表，使快速记账账户选择不会访问真实后端。 */
async function mockAccounts(page: Page) {
  await page.route('**/api/v1/securities/lookup**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ securities: [{ id: 'security-1', market: 'SH', symbol: '600519', name: '贵州茅台', currency: 'CNY' }], error: null }) }))
  await page.route('**/api/v1/securities?query=', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }))
  await page.route('**/api/v1/accounts', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([{ id: 'account-cny', name: 'A股账户', currency: 'CNY' }]),
  }))
}

test.describe('portfolio journal dashboard', () => {
  test('navigates between the main workspaces', async ({ page }) => {
    await page.route('**/api/v1/dashboard**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(makeDashboard({ positions: [], quotes: [], summary: makeSummary({ valuationStatus: 'empty', totalOpenPositions: 0, quotedOpenPositions: 0, unrealizedPnl: '0.0000', unrealizedReturnPct: null }) })) }))
    await mockAccounts(page)
    await page.route('**/api/v1/trades', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }))
    await page.route('**/api/v1/cycles**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }))
    await page.route('**/api/v1/analytics/summary', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(makeSummary()) }))

    await page.goto('/dashboard')
    await expect(page.getByRole('heading', { name: '总览', exact: true })).toBeVisible()
    await page.getByText('交易流水 Trades').click()
    await expect(page.getByRole('heading', { name: '交易流水' })).toBeVisible()
    await page.getByText('复盘分析 Analytics').click()
    await expect(page.getByRole('heading', { name: '复盘分析' })).toBeVisible()
  })

  test('shows the cache snapshot before replacing it with the auto result', async ({ page }) => {
    const modes: string[] = []
    await mockAccounts(page)
    await page.route('**/api/v1/dashboard**', async (route) => {
      const mode = new URL(route.request().url()).searchParams.get('mode') ?? ''
      modes.push(mode)
      if (mode === 'cache') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makeDashboard({
            positions: [makePosition({ quote: makeQuote({ lastPrice: '8.000000', changePct: '-2.000000' }), unrealizedPnl: '-200.0000', unrealizedReturnPct: '-20.000000' })],
            quotes: [makeQuote({ lastPrice: '8.000000', changePct: '-2.000000' })],
          })),
        })
        return
      }
      await new Promise((resolve) => setTimeout(resolve, 350))
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(makeDashboard({
          positions: [makePosition({ quote: makeQuote({ lastPrice: '10.000000', changePct: '5.263158' }), unrealizedPnl: '0.0000', unrealizedReturnPct: '0.000000' })],
        })),
      })
    })

    await page.goto('/dashboard')
    const row = page.locator('.position-table .el-table__row').filter({ hasText: 'SH:600519' })
    await expect(row).toContainText('¥ 8.00')
    await expect(row).toContainText('-200.00')
    await expect(row).toContainText('¥ 10.00')
    await expect(row).toContainText('¥ 0.00')
    expect(modes.slice(0, 2)).toEqual(['cache', 'auto'])
  })

  test('reuses in-flight auto requests and pauses polling while hidden', async ({ page }) => {
    let activeRequests = 0
    let maxActiveRequests = 0
    let autoRequests = 0
    let manualRequests = 0
    let resolveFirstAuto: (() => void) | null = null
    const firstAutoStarted = new Promise<void>((resolve) => { resolveFirstAuto = resolve })

    await mockAccounts(page)
    await page.route('**/api/v1/dashboard**', async (route) => {
      const mode = new URL(route.request().url()).searchParams.get('mode')
      activeRequests += 1
      maxActiveRequests = Math.max(maxActiveRequests, activeRequests)
      try {
        if (mode === 'auto') {
          autoRequests += 1
          if (autoRequests === 1) {
            resolveFirstAuto?.()
            await new Promise((resolve) => setTimeout(resolve, 400))
          }
        }
        if (mode === 'manual') manualRequests += 1
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(makeDashboard({ pollAfterSeconds: 1 })) })
      } finally {
        activeRequests -= 1
      }
    })

    await page.goto('/dashboard')
    await firstAutoStarted
    await page.getByRole('button', { name: '刷新行情' }).click()
    await expect.poll(() => manualRequests).toBe(1)
    expect(maxActiveRequests).toBe(1)
    await page.evaluate(() => {
      const testWindow = window as Window & { __documentHidden?: boolean }
      testWindow.__documentHidden = false
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => Boolean(testWindow.__documentHidden) })
      document.dispatchEvent(new Event('visibilitychange'))
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await expect.poll(() => autoRequests).toBeGreaterThanOrEqual(2)
    expect(maxActiveRequests).toBe(1)

    await page.evaluate(() => {
      const testWindow = window as Window & { __documentHidden?: boolean }
      testWindow.__documentHidden = true
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await page.waitForTimeout(250)
    const pausedCount = autoRequests
    await page.waitForTimeout(1400)
    expect(autoRequests).toBe(pausedCount)
    expect(maxActiveRequests).toBe(1)

    await page.evaluate(() => {
      const testWindow = window as Window & { __documentHidden?: boolean }
      testWindow.__documentHidden = false
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await expect.poll(() => autoRequests).toBeGreaterThan(pausedCount)
    expect(maxActiveRequests).toBe(1)
  })

  test('reloads the whole valuation after buy while preserving includeClosed', async ({ page }) => {
    const dashboardCalls: Array<{ mode: string; includeClosed: boolean }> = []
    await mockAccounts(page)
    await page.route('**/api/v1/dashboard**', async (route) => {
      const url = new URL(route.request().url())
      dashboardCalls.push({ mode: url.searchParams.get('mode') ?? '', includeClosed: url.searchParams.get('includeClosed') === 'true' })
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(makeDashboard({ positions: [], quotes: [], summary: makeSummary({ valuationStatus: 'empty', totalOpenPositions: 0, quotedOpenPositions: 0, unrealizedPnl: '0.0000', unrealizedReturnPct: null }) })) })
    })
    await page.route('**/api/v1/securities?query=600519', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ id: 'security-1', market: 'SH', symbol: '600519', name: '贵州茅台', currency: 'CNY' }]) }))
    await page.route('**/api/v1/securities/security-1/lots**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ lots: [{ id: 'lot-1', status: 'OPEN' }], closedLotCount: 0, realizedPnl: '0.0000' }) }))
    await page.route('**/api/v1/trades/buy', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(makeTrade()) }))

    await page.goto('/dashboard')
    await page.getByText('显示已结清').click()
    await expect.poll(() => dashboardCalls.some((call) => call.includeClosed)).toBe(true)

    await page.getByPlaceholder('输入证券代码、名称，例如 600519').fill('600519')
    await page.getByRole('button', { name: '查找证券' }).click()
    await page.getByRole('button', { name: /SH:600519/ }).click()
    await expect(page.getByRole('button', { name: '买入记账' })).toBeVisible()
    await expect(page.getByRole('button', { name: '卖出配对' })).toBeVisible()

    const countBeforeBuy = dashboardCalls.length
    await page.getByRole('button', { name: '买入记账' }).click()
    const dialog = page.getByRole('dialog', { name: /录入买入成交/ })
    await dialog.locator('input').nth(0).fill('100')
    await dialog.locator('input').nth(1).fill('10')
    await dialog.getByRole('button', { name: '保存买入' }).click()

    await expect.poll(() => dashboardCalls.length).toBeGreaterThan(countBeforeBuy)
    expect(dashboardCalls.at(-1)).toEqual({ mode: 'auto', includeClosed: true })
  })

  /** 验证最近买入详情、输入失效和旧预览响应隔离。 */
  test('previews equal buy details and submits only the current preview', async ({ page }) => {
    const previewBodies: Array<{ qty: string; matchingMethod?: string }> = []
    const sellBodies: Array<{ qty: string; matches: Array<{ lotId: string; matchedQty: string }> }> = []
    let sold = false
    let releaseSlowPreview = () => {}
    const slowPreview = new Promise<void>((resolve) => { releaseSlowPreview = resolve })
    await mockAccounts(page)
    await page.route('**/api/v1/dashboard**', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(sold
        ? makeDashboard({ positions: [makePosition({ remainingQty: '6.0000', realizedPnl: '52.0000' })], summary: makeSummary({ realizedPnl: '52.0000', remainingQty: '6.0000' }) })
        : makeDashboard({ positions: [], quotes: [], summary: makeSummary({ valuationStatus: 'empty', totalOpenPositions: 0, quotedOpenPositions: 0, unrealizedPnl: '0.0000', unrealizedReturnPct: null }) })),
    }))
    await page.route('**/api/v1/securities?query=600519', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ id: 'security-1', market: 'SH', symbol: '600519', name: '贵州茅台', currency: 'CNY' }]) }))
    await page.route('**/api/v1/securities/security-1/lots**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ lots: [{ id: 'lot-newer-1234567890', status: 'OPEN' }], closedLotCount: 0, realizedPnl: '0.0000' }) }))
    await page.route('**/api/v1/trades/sell/preview', async (route) => {
      const payload = route.request().postDataJSON() as { qty: string; matchingMethod?: string }
      previewBodies.push(payload)
      if (payload.qty === '3') await slowPreview
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(makeSellPreview(payload.qty)) })
    })
    await page.route('**/api/v1/trades/sell', (route) => {
      sellBodies.push(route.request().postDataJSON())
      sold = true
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ...makeTrade(), side: 'SELL' }) })
    })

    await page.goto('/dashboard')
    await page.getByPlaceholder('输入证券代码、名称，例如 600519').fill('600519')
    await page.getByRole('button', { name: '查找证券' }).click()
    await page.getByRole('button', { name: /SH:600519/ }).click()
    await expect(page.getByRole('button', { name: '卖出配对' })).toBeEnabled()
    await page.getByRole('button', { name: '卖出配对' }).click()
    const drawer = page.getByRole('dialog', { name: /卖出配对工作台/ })
    const inputs = drawer.locator('input')
    await inputs.nth(0).fill('2')
    await inputs.nth(1).fill('30')
    await drawer.getByRole('button', { name: '预览最近优先等量配对' }).click()

    await expect.poll(() => previewBodies.length).toBe(1)
    expect(previewBodies[0]?.matchingMethod).toBe('EQUAL')
    const row = drawer.locator('.el-table__row')
    await expect(row).toContainText('原始买入 10 · 预览前可用 10')
    await expect(row).toContainText('¥ 20.00')
    await expect(row).toContainText('lot-newe')
    await expect(row.locator('.lot-code')).toHaveAttribute('title', 'lot-newer-1234567890')

    await inputs.nth(0).fill('3')
    const confirm = drawer.getByRole('button', { name: '确认卖出' })
    await expect(confirm).toBeDisabled()
    await expect(drawer.getByText('尚无有效预览')).toBeVisible()
    await drawer.getByRole('button', { name: '预览最近优先等量配对' }).click()
    await expect.poll(() => previewBodies.length).toBe(2)
    await inputs.nth(0).fill('4')
    await expect(confirm).toBeDisabled()
    await drawer.getByRole('button', { name: '预览最近优先等量配对' }).click()
    await expect.poll(() => previewBodies.length).toBe(3)
    await expect(row.locator('.match-primary').nth(2)).toHaveText('4')

    const slowResponse = page.waitForResponse((response) => response.url().includes('/trades/sell/preview') && response.request().postDataJSON().qty === '3')
    releaseSlowPreview()
    await slowResponse
    await expect(confirm).toBeEnabled()
    await expect(row.locator('.match-primary').nth(2)).toHaveText('4')
    await confirm.click()
    await expect.poll(() => sellBodies.length).toBe(1)
    expect(sellBodies[0]).toMatchObject({ qty: '4.0000', matches: [{ lotId: 'lot-newer-1234567890', matchedQty: '4.0000' }] })
    const positionRow = page.locator('.position-table .el-table__row').filter({ hasText: 'SH:600519' })
    await expect(positionRow.locator('td').nth(1)).toHaveText('6')
  })

  test('distinguishes zero, stale and unavailable values without showing a partial total', async ({ page }) => {
    const partialDashboard = makeDashboard({
      positions: [
        makePosition({ quote: makeQuote({ changePct: '0.000000' }), unrealizedPnl: '0.0000', unrealizedReturnPct: '0.000000', lowestPriceGroup: null }),
        makePosition({
          securityId: 'security-2',
          market: 'SZ',
          symbol: '000001',
          name: '平安银行',
          quote: makeQuote({ symbol: 'SZ:000001', securityId: 'security-2', lastPrice: '8.000000', changePct: '-1.000000', isStale: true }),
          unrealizedPnl: '-100.0000',
          unrealizedReturnPct: '-10.000000',
          lowestPriceGroup: {
            purchasePrice: '8.000000',
            lotCount: 1,
            remainingQty: '100.0000',
            remainingCost: '1000.0000',
            unrealizedPnl: null,
            unrealizedReturnPct: null,
          },
        }),
        makePosition({ securityId: 'security-3', market: 'SZ', symbol: '300001', name: '特锐德', quote: null, unrealizedPnl: null, unrealizedReturnPct: null, lowestPriceGroup: null }),
      ],
      quotes: [
        makeQuote({ changePct: '0.000000' }),
        makeQuote({ symbol: 'SZ:000001', securityId: 'security-2', lastPrice: '8.000000', changePct: '-1.000000', isStale: true }),
      ],
      summary: makeSummary({
        valuationStatus: 'partial',
        totalOpenPositions: 3,
        quotedOpenPositions: 2,
        staleOpenPositions: 1,
        unrealizedPnl: '999.0000',
        unrealizedReturnPct: '9.000000',
      }),
    })
    await mockAccounts(page)
    await page.route('**/api/v1/dashboard**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(partialDashboard) }))

    await page.goto('/dashboard')
    await expect(page.getByText('行情覆盖不完整', { exact: true })).toBeVisible()
    await expect(page.getByText('账户浮盈不可用', { exact: true })).toBeVisible()
    await expect(page.getByText('¥ 999.00')).toHaveCount(0)

    const zeroRow = page.locator('.position-table .el-table__row').filter({ hasText: 'SH:600519' })
    await expect(zeroRow).toContainText('0.00%')
    await expect(zeroRow).toContainText('¥ 0.00')

    const staleRow = page.locator('.position-table .el-table__row').filter({ hasText: 'SZ:000001' })
    await expect(staleRow).toContainText('已过期')
    await expect(staleRow).toContainText('组浮盈 — / —')

    const missingRow = page.locator('.position-table .el-table__row').filter({ hasText: 'SZ:300001' })
    await expect(missingRow).toContainText('行情不可用')
  })

  test('keeps ledger actions usable when the dashboard request fails', async ({ page }) => {
    await mockAccounts(page)
    await page.route('**/api/v1/dashboard**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { message: '行情服务暂时不可用' } }) }))

    await page.goto('/dashboard')
    await expect(page.getByText(/估值刷新失败：行情服务暂时不可用/)).toBeVisible()
    await expect(page.getByPlaceholder('输入证券代码、名称，例如 600519')).toBeVisible()
    await expect(page.getByRole('button', { name: '刷新行情' })).toBeVisible()
  })
})
