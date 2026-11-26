import { expect, test, type Page, type Route } from '@playwright/test'
import type { Lot } from '../src/api/types'

/** 统一返回 JSON 模拟响应，避免 E2E 访问真实后端或本地账本。 */
async function respond(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/** 构造同买入价不同手续费单价及不同买入价的持仓批次夹具。 */
function fixtureLots(includeClosed: boolean): Lot[] {
  const openLots: Lot[] = [
    {
      id: '11111111-lot-ten-newer',
      buyTradeId: 'buy-1',
      accountId: 'account-a',
      securityId: 'security-a',
      strategyId: 'default',
      cycleId: 'cycle-a',
      originalQty: '200.0000',
      originalCost: '2005.0000',
      remainingQty: '200.0000',
      remainingCost: '2005.0000',
      unitCost: '10.025000',
      purchasePrice: '10.000000',
      executedAt: '2026-11-20T02:00:00Z',
      status: 'OPEN',
      targets: [],
      unrealizedPnl: '195.0000',
      unrealizedReturnPct: '9.725686',
      effectiveTarget: {
        source: 'GLOBAL',
        targetReturnPct: '0.150000',
        calculationBasis: 'NET',
        targetPriceEstimate: '11.528750',
        alertEnabled: true,
      },
    },
    {
      id: '22222222-lot-ten-older',
      buyTradeId: 'buy-2',
      accountId: 'account-a',
      securityId: 'security-a',
      strategyId: 'default',
      cycleId: 'cycle-a',
      originalQty: '150.0000',
      originalCost: '1507.5000',
      remainingQty: '100.0000',
      remainingCost: '1005.0000',
      unitCost: '10.050000',
      purchasePrice: '10.000000',
      executedAt: '2026-11-18T02:00:00Z',
      status: 'OPEN',
      targets: [],
      unrealizedPnl: '95.0000',
      unrealizedReturnPct: '9.452736',
      effectiveTarget: {
        source: 'GLOBAL',
        targetReturnPct: '0.150000',
        calculationBasis: 'NET',
        targetPriceEstimate: '11.557500',
        alertEnabled: true,
      },
    },
    {
      id: '33333333-lot-twelve',
      buyTradeId: 'buy-3',
      accountId: 'account-a',
      securityId: 'security-a',
      strategyId: 'default',
      cycleId: 'cycle-a',
      originalQty: '100.0000',
      originalCost: '1205.0000',
      remainingQty: '100.0000',
      remainingCost: '1205.0000',
      unitCost: '12.050000',
      purchasePrice: '12.000000',
      executedAt: '2026-11-19T02:00:00Z',
      status: 'OPEN',
      targets: [],
      unrealizedPnl: '-105.0000',
      unrealizedReturnPct: '-8.713693',
      effectiveTarget: {
        source: 'GLOBAL',
        targetReturnPct: '0.150000',
        calculationBasis: 'NET',
        targetPriceEstimate: '13.857500',
        alertEnabled: true,
      },
    },
  ]
  if (!includeClosed) return openLots
  return [
    ...openLots,
    {
      id: '44444444-closed-ten',
      buyTradeId: 'buy-4',
      accountId: 'account-a',
      securityId: 'security-a',
      strategyId: 'default',
      cycleId: 'cycle-a',
      originalQty: '50.0000',
      originalCost: '502.5000',
      remainingQty: '0.0000',
      remainingCost: '0.0000',
      unitCost: '10.050000',
      purchasePrice: '10.000000',
      executedAt: '2026-11-15T02:00:00Z',
      status: 'CLOSED',
      targets: [],
      unrealizedPnl: null,
      unrealizedReturnPct: null,
      effectiveTarget: null,
    },
  ]
}

/** 拦截个股详情接口，提供稳定的多批次 mock 数据。 */
async function mockLotMergePage(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    if (path === '/auth/me') {
      return respond(route, {
        user: {
          id: 'admin-1',
          email: 'admin@localhost',
          displayName: '默认管理员',
          role: 'ADMIN',
          status: 'ACTIVE',
          isDefaultAdmin: true,
          createdAt: '2026-11-18T00:00:00Z',
        },
      })
    }
    if (path === '/accounts') {
      return respond(route, [{ id: 'account-a', name: '主账户', currency: 'CNY' }])
    }
    if (path === '/dashboard') {
      return respond(route, {
        accountId: 'account-a',
        currency: 'CNY',
        asOf: '2026-11-20T03:00:00Z',
        pollAfterSeconds: 60,
        summary: {
          marketValue: '4400.0000',
          totalBuyCashOut: '4717.5000',
          totalSellCashIn: '550.0000',
          realizedPnl: '47.5000',
          unrealizedPnl: '185.0000',
          unrealizedReturnPct: '4.389087',
          valuationStatus: 'fresh',
          totalOpenPositions: 1,
          quotedOpenPositions: 1,
          staleOpenPositions: 0,
        },
        pendingRefreshScopes: [],
        positions: [],
        quotes: [],
      })
    }
    if (path === '/securities') {
      return respond(route, [{ id: 'security-a', market: 'SH', symbol: '600519', name: '贵州茅台', currency: 'CNY' }])
    }
    if (path.endsWith('/intraday')) {
      return respond(route, {
        symbol: 'SH:600519',
        currency: 'CNY',
        tradingDate: '2026-11-20',
        points: [{ time: '2026-11-20T02:00:00Z', price: '11.000000' }],
        quote: {
          symbol: 'SH:600519',
          securityId: 'security-a',
          market: 'SH',
          currency: 'CNY',
          lastPrice: '11.000000',
          prevClose: '10.800000',
          changePct: '1.851852',
          sourceTime: '2026-11-20T02:00:00Z',
          fetchedAt: '2026-11-20T02:00:00Z',
          provider: 'fixture',
          isStale: false,
        },
        provider: 'fixture',
        fetchedAt: '2026-11-20T02:00:00Z',
        isStale: false,
        error: null,
      })
    }
    if (path.endsWith('/lots')) {
      const includeClosed = url.searchParams.get('includeClosed') === 'true'
      return respond(route, {
        lots: fixtureLots(includeClosed),
        accountId: 'account-a',
        strategyId: 'default',
        needsRefresh: false,
        closedLotCount: 1,
        realizedPnl: '47.5000',
        summary: {
          remainingQty: '400.0000',
          remainingCost: '4215.0000',
          realizedPnl: '47.5000',
          unrealizedPnl: '185.0000',
        },
        quote: {
          symbol: 'SH:600519',
          securityId: 'security-a',
          market: 'SH',
          currency: 'CNY',
          lastPrice: '11.000000',
          prevClose: '10.800000',
          changePct: '1.851852',
          sourceTime: '2026-11-20T02:00:00Z',
          fetchedAt: '2026-11-20T02:00:00Z',
          provider: 'fixture',
          isStale: false,
        },
        currency: 'CNY',
        closedTradeDetails: [],
      })
    }
    return respond(route, [])
  })
}

test('持仓批次默认按买入价格合并，支持展开明细、合并卖出与开关切换', async ({ page }) => {
  await mockLotMergePage(page)
  await page.goto('/security/SH/600519')

  const table = page.locator('.lot-table')
  const rows = table.locator('.el-table__row')

  // 默认按买入价合并：两笔 10.000 合并为 1 行，一笔 12.000 独立 1 行，共 2 行
  await expect(rows).toHaveCount(2)
  const mergedRow = rows.first()
  await expect(mergedRow).toContainText('合并 2 批')
  await expect(mergedRow).toContainText('剩 300')
  await expect(mergedRow).toContainText('/ 初 350')
  await expect(mergedRow).toContainText('买入价 ¥ 10.000')
  await expect(mergedRow).toContainText('含费均价 ¥ 10.033')
  await expect(mergedRow).toContainText('成本 ¥ 3,010.00')
  await expect(mergedRow).toContainText('¥ 290.00')
  await expect(mergedRow).toContainText('9.63%')
  await expect(mergedRow).toContainText('15.00%')
  await expect(mergedRow).toContainText('继承全局')

  // 点击合并行“卖出”应预填合并总剩余数量 300
  await mergedRow.getByRole('button', { name: '卖出', exact: true }).click()
  const sellDrawer = page.getByRole('dialog', { name: /卖出配对工作台/ })
  await expect(sellDrawer).toBeVisible()
  await expect(sellDrawer.getByPlaceholder(/例如/)).toHaveValue('300')
  await sellDrawer.getByRole('button', { name: '关闭' }).or(sellDrawer.locator('.el-drawer__close-btn')).first().click()

  // 展开合并行后可见两条原始子批次及其独立设置按钮
  await mergedRow.getByRole('button', { name: '明细', exact: true }).click()
  await expect(table).toContainText('11111111')
  await expect(table).toContainText('22222222')

  // 开启“显示已结清”时，同价已结清批次不与持有中批次混淆合并
  await page.getByText('显示已结清', { exact: true }).click()
  await expect(table).toContainText('44444444')

  // 关闭“按买入价合并”后恢复逐批平铺展示（3 笔持有中 + 1 笔已结清 = 4 行）
  await page.getByText('按买入价合并', { exact: true }).click()
  await expect(rows).toHaveCount(4)
  await expect(table).not.toContainText('合并 2 批')
})

test('移动端持仓批次默认按买入价格合并并支持卡片内展开子批次', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockLotMergePage(page)
  await page.goto('/security/SH/600519')

  const cards = page.locator('.m-lot-item')
  await expect(cards).toHaveCount(2)
  const mergedCard = cards.first()
  await expect(mergedCard).toContainText('合并 2 批')
  await expect(mergedCard).toContainText('300 / 350')
  await expect(mergedCard).toContainText('买价 ¥ 10.000')

  await mergedCard.getByRole('button', { name: /合并 2 批/ }).click()
  await expect(mergedCard.locator('.m-lot-child')).toHaveCount(2)
  await expect(mergedCard).toContainText('11111111')
  await expect(mergedCard).toContainText('22222222')

  // 验证无横向溢出
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(overflow).toBe(false)
})
