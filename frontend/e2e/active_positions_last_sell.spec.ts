import { expect, test, type Page, type Route } from '@playwright/test'

/** 返回 JSON 模拟响应。 */
function respondJson(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

/** 构造用于验证上次卖出价、筛选、五维排序和 PC 右栏详情联动的 mock 环境。 */
async function mockWorkspaceApi(page: Page) {
  const adminUser = {
    id: 'admin-1',
    email: 'admin@localhost',
    displayName: '默认管理员',
    role: 'ADMIN',
    status: 'ACTIVE',
    isDefaultAdmin: true,
    createdAt: '2026-11-18T00:00:00Z',
  }
  const securities = [
    { id: 'sec-mr', market: 'SZ', symbol: '300760', name: '迈瑞医疗', securityType: 'STOCK', currency: 'CNY' },
    { id: 'sec-nd', market: 'SZ', symbol: '300750', name: '宁德时代', securityType: 'STOCK', currency: 'CNY' },
    { id: 'sec-md', market: 'SZ', symbol: '000333', name: '美的集团', securityType: 'STOCK', currency: 'CNY' },
  ]
  const positions = [
    {
      securityId: 'sec-mr',
      market: 'SZ',
      symbol: '300760',
      name: '迈瑞医疗',
      securityType: 'STOCK',
      currency: 'CNY',
      strategyId: 'strat-1',
      remainingQty: '100.0000',
      remainingCost: '25280.0000',
      averageCost: '252.800000',
      realizedPnl: '0.0000',
      unrealizedPnl: '360.0000',
      unrealizedReturnPct: '1.424051',
      strategyBreakevenPrice: '252.800000',
      fundsRecoveredSurplus: null,
      lastSellPrice: null,
      lastSellExecutedAt: null,
      vsLastSellPct: null,
      quote: {
        symbol: 'SZ:300760',
        securityId: 'sec-mr',
        market: 'SZ',
        currency: 'CNY',
        lastPrice: '256.400000',
        prevClose: '259.090000',
        changePct: '-1.040000',
        sourceTime: '2026-11-25T06:00:00Z',
        fetchedAt: '2026-11-25T06:00:01Z',
        provider: 'fixture',
        isStale: false,
        marketSession: 'regular',
      },
      lowestPriceGroup: {
        purchasePrice: '252.800000',
        lotCount: 1,
        remainingQty: '100.0000',
        remainingCost: '25280.0000',
        unrealizedPnl: '360.0000',
        unrealizedReturnPct: '1.424051',
      },
      needsRefresh: false,
    },
    {
      securityId: 'sec-nd',
      market: 'SZ',
      symbol: '300750',
      name: '宁德时代',
      securityType: 'STOCK',
      currency: 'CNY',
      strategyId: 'strat-1',
      remainingQty: '100.0000',
      remainingCost: '24200.0000',
      averageCost: '242.000000',
      realizedPnl: '1200.0000',
      unrealizedPnl: '660.0000',
      unrealizedReturnPct: '2.727273',
      strategyBreakevenPrice: '230.000000',
      fundsRecoveredSurplus: null,
      lastSellPrice: '264.500000',
      lastSellExecutedAt: '2026-11-20T06:00:00+08:00',
      vsLastSellPct: '-6.011342',
      quote: {
        symbol: 'SZ:300750',
        securityId: 'sec-nd',
        market: 'SZ',
        currency: 'CNY',
        lastPrice: '248.600000',
        prevClose: '251.390000',
        changePct: '-1.110000',
        sourceTime: '2026-11-25T06:00:00Z',
        fetchedAt: '2026-11-25T06:00:01Z',
        provider: 'fixture',
        isStale: false,
        marketSession: 'regular',
      },
      lowestPriceGroup: {
        purchasePrice: '242.000000',
        lotCount: 1,
        remainingQty: '100.0000',
        remainingCost: '24200.0000',
        unrealizedPnl: '660.0000',
        unrealizedReturnPct: '2.727273',
      },
      needsRefresh: false,
    },
    {
      securityId: 'sec-md',
      market: 'SZ',
      symbol: '000333',
      name: '美的集团',
      securityType: 'STOCK',
      currency: 'CNY',
      strategyId: 'strat-1',
      remainingQty: '300.0000',
      remainingCost: '21790.0000',
      averageCost: '72.633333',
      realizedPnl: '500.0000',
      unrealizedPnl: '-355.0000',
      unrealizedReturnPct: '-1.629188',
      strategyBreakevenPrice: '70.966667',
      fundsRecoveredSurplus: null,
      lastSellPrice: '75.800000',
      lastSellExecutedAt: '2026-11-18T06:00:00+08:00',
      vsLastSellPct: '-5.738786',
      quote: {
        symbol: 'SZ:000333',
        securityId: 'sec-md',
        market: 'SZ',
        currency: 'CNY',
        lastPrice: '71.450000',
        prevClose: '72.180000',
        changePct: '-1.010000',
        sourceTime: '2026-11-25T06:00:00Z',
        fetchedAt: '2026-11-25T06:00:01Z',
        provider: 'fixture',
        isStale: false,
        marketSession: 'regular',
      },
      lowestPriceGroup: {
        purchasePrice: '70.200000',
        lotCount: 1,
        remainingQty: '100.0000',
        remainingCost: '7020.0000',
        unrealizedPnl: '125.0000',
        unrealizedReturnPct: '1.780627',
      },
      needsRefresh: false,
    },
  ]

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    if (path === '/auth/me') return respondJson(route, { user: adminUser })
    if (path === '/accounts') return respondJson(route, [{ id: 'acc-1', name: 'A股主账户', currency: 'CNY', broker: null }])
    if (path === '/dashboard') {
      return respondJson(route, {
        accountId: 'acc-1',
        currency: 'CNY',
        asOf: '2026-11-25T14:00:00+08:00',
        marketSessionOpen: true,
        marketSession: 'regular',
        pollAfterSeconds: 10,
        positions,
        pendingRefreshScopes: [],
        summary: {
          currency: 'CNY',
          totalBuyCashOut: '71270.0000',
          totalSellCashIn: '5000.0000',
          realizedPnl: '1700.0000',
          unrealizedPnl: '665.0000',
          marketValue: '71935.0000',
          unrealizedReturnPct: '0.933071',
          remainingQty: '500.0000',
          remainingCost: '71270.0000',
          valuationStatus: 'fresh',
          totalOpenPositions: 3,
          quotedOpenPositions: 3,
          staleOpenPositions: 0,
        },
        quotes: positions.map((p) => p.quote),
      })
    }
    if (path === '/securities') {
      const q = (url.searchParams.get('query') ?? '').toLowerCase()
      const matched = q ? securities.filter((s) => s.symbol.includes(q) || s.name.includes(q)) : securities
      return respondJson(route, matched)
    }
    const lotsMatch = path.match(/^\/securities\/([^/]+)\/lots$/)
    if (lotsMatch) {
      const pos = positions.find((item) => item.securityId === lotsMatch[1]) ?? positions[0]!
      return respondJson(route, {
        lots: [],
        accountId: 'acc-1',
        strategyId: 'strat-1',
        needsRefresh: false,
        closedLotCount: 0,
        realizedPnl: pos.realizedPnl,
        summary: {
          remainingQty: pos.remainingQty,
          remainingCost: pos.remainingCost,
          realizedPnl: pos.realizedPnl,
          unrealizedPnl: pos.unrealizedPnl,
          lastSellPrice: pos.lastSellPrice,
          lastSellExecutedAt: pos.lastSellExecutedAt,
          vsLastSellPct: pos.vsLastSellPct,
        },
        quote: pos.quote,
        currency: 'CNY',
        closedTradeDetails: [],
      })
    }
    const intradayMatch = path.match(/^\/securities\/([^/]+)\/intraday$/)
    if (intradayMatch) {
      const pos = positions.find((item) => item.securityId === intradayMatch[1]) ?? positions[0]!
      return respondJson(route, {
        symbol: `${pos.market}:${pos.symbol}`,
        currency: 'CNY',
        tradingDate: '2026-11-25',
        seriesKind: 'intraday',
        points: [
          { time: '2026-11-25T09:30:00+08:00', price: pos.quote.prevClose },
          { time: '2026-11-25T14:00:00+08:00', price: pos.quote.lastPrice },
        ],
        quote: pos.quote,
        provider: 'fixture',
        fetchedAt: '2026-11-25T14:00:01+08:00',
        isStale: false,
        error: null,
      })
    }
    return respondJson(route, [])
  })
}

test('PC 端活动仓位展示上次卖出价、筛选、五维正反排序并联动右栏个股详情', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockWorkspaceApi(page)
  await page.goto('/dashboard')

  const rows = page.locator('.ap-list .ap-row')
  await expect(rows).toHaveCount(3)

  // 默认按当日涨幅（现价）降序：美的(-1.01%) > 迈瑞(-1.04%) > 宁德(-1.11%)
  await expect(rows.nth(0)).toContainText('SZ:000333')
  await expect(rows.nth(1)).toContainText('SZ:300760')
  await expect(rows.nth(2)).toContainText('SZ:300750')

  // 验证上次卖出价与无卖出空态
  await expect(rows.nth(1)).toContainText('暂无卖出')
  await expect(rows.nth(2)).toContainText('264.500')
  await expect(rows.nth(2)).toContainText('-6.01%')

  // 验证 PC 右栏默认联动展示排序第一只（美的集团）的详情及上次卖出价
  const detail = page.locator('.workspace-detail')
  await expect(detail).toContainText('SZ:000333 美的集团')
  await expect(detail.locator('.holding-summary')).toContainText('上次卖出价')
  await expect(detail.locator('.holding-summary')).toContainText('¥ 75.800')
  await expect(detail.locator('.holding-summary')).toContainText('-5.74%')

  // 点击左侧宁德时代，右栏即时切换为宁德时代
  await rows.nth(2).click()
  await expect(detail).toContainText('SZ:300750 宁德时代')
  await expect(detail.locator('.holding-summary')).toContainText('¥ 264.500')

  // 验证筛选标签数量角标与过滤
  await expect(page.getByRole('tab', { name: /全部\s*3/ })).toBeVisible()
  await expect(page.getByRole('tab', { name: /低批盈利\s*3/ })).toBeVisible()
  await expect(page.getByRole('tab', { name: /低于上卖\s*2/ })).toBeVisible()
  await expect(page.getByRole('tab', { name: /浮亏仓位\s*1/ })).toBeVisible()

  await page.getByRole('tab', { name: /低于上卖/ }).click()
  await expect(rows).toHaveCount(2)
  await page.getByRole('tab', { name: /浮亏仓位/ }).click()
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText('美的集团')
  await page.getByRole('tab', { name: /全部/ }).click()
  await expect(rows).toHaveCount(3)

  // 验证五维排序：总市值降序、低批升序、上卖正反排序（无卖出的迈瑞始终置后）
  await page.locator('.ap-th-val .ap-sort-btn', { hasText: '总市值' }).click()
  await expect(rows.nth(0)).toContainText('SZ:300760')
  await expect(rows.nth(1)).toContainText('SZ:300750')
  await expect(rows.nth(2)).toContainText('SZ:000333')

  await page.locator('.ap-th-prices .ap-sort-btn', { hasText: '上卖' }).click()
  await expect(rows.nth(0)).toContainText('SZ:000333')
  await expect(rows.nth(1)).toContainText('SZ:300750')
  await expect(rows.nth(2)).toContainText('SZ:300760')

  await page.locator('.ap-th-prices .ap-sort-btn', { hasText: '上卖' }).click()
  await expect(rows.nth(0)).toContainText('SZ:300750')
  await expect(rows.nth(1)).toContainText('SZ:000333')
  await expect(rows.nth(2)).toContainText('SZ:300760')

  // 验证代码/名称搜索与紧凑切换
  await page.getByPlaceholder('代码 / 名称').fill('迈瑞')
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText('SZ:300760')
  await page.getByPlaceholder('代码 / 名称').fill('')
  await page.locator('.ap-compact-btn').click()
  await expect(page.locator('.ap-card')).toHaveClass(/is-compact/)
})
