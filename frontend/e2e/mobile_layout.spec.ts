import { expect, test, type Page, type Route } from '@playwright/test'

/** 统一返回模拟 JSON 响应，不访问真实后端或本地 ledger.db。 */
async function respond(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/** 断言当前视口下不存在页面级横向滚动溢出。 */
async function assertNoHorizontalOverflow(page: Page): Promise<void> {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }))
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.innerWidth)
}

/** 构造移动端全业务流程所需的模拟接口，覆盖总览、详情、流水、复盘与全部弹窗。 */
async function mockMobileApi(page: Page, authenticated = true) {
  const state = {
    buyCount: 0,
    sellCount: 0,
    targetSaved: 0,
    editSaved: 0,
  }

  await page.route('**/api/v1/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace('/api/v1', '')

    if (path === '/auth/me') {
      if (!authenticated) return respond(route, { detail: '未登录' }, 401)
      return respond(route, {
        user: {
          id: 'user-1',
          email: 'trader@example.com',
          displayName: '高频交易员',
          role: 'USER',
          status: 'ACTIVE',
          isDefaultAdmin: false,
          createdAt: '2026-11-18T00:00:00Z',
        },
      })
    }

    if (path === '/accounts') {
      return respond(route, [{ id: 'acc-cny', name: 'A股主账户', currency: 'CNY' }])
    }

    if (path === '/securities') {
      return respond(route, [
        { id: 'sec-1', market: 'SH', symbol: '600519', name: '贵州茅台', currency: 'CNY' },
        { id: 'sec-2', market: 'SZ', symbol: '000001', name: '平安银行', currency: 'CNY' },
        { id: 'sec-3', market: 'SH', symbol: '601318', name: '中国平安', currency: 'CNY' },
      ])
    }

    if (path === '/securities/lookup') {
      return respond(route, {
        securities: [{ id: 'sec-1', market: 'SH', symbol: '600519', name: '贵州茅台', currency: 'CNY' }],
        error: null,
      })
    }

    if (path === '/dashboard') {
      return respond(route, {
        accountId: 'acc-cny',
        currency: 'CNY',
        asOf: '2026-11-20T06:30:00Z',
        pollAfterSeconds: 60,
        summary: {
          marketValue: '52800.00',
          totalBuyCashOut: '48000.00',
          totalSellCashIn: '6200.00',
          realizedPnl: '1250.00',
          unrealizedPnl: '3550.00',
          unrealizedReturnPct: '7.21',
          valuationStatus: 'fresh',
          totalOpenPositions: 3,
          quotedOpenPositions: 2,
          staleOpenPositions: 0,
        },
        pendingRefreshScopes: [],
        positions: [
          {
            securityId: 'sec-2',
            strategyId: 'default',
            market: 'SZ',
            symbol: '000001',
            name: '平安银行',
            currency: 'CNY',
            remainingQty: '1000.0000',
            remainingCost: '11000.00',
            averageCost: '11.000',
            unrealizedPnl: '-250.00',
            unrealizedReturnPct: '-2.27',
            strategyBreakevenPrice: '11.020',
            needsRefresh: false,
            lowestPriceGroup: {
              purchasePrice: '10.800',
              lotCount: 1,
              remainingQty: '500.0000',
              remainingCost: '5400.00',
              unrealizedPnl: '-25.00',
              unrealizedReturnPct: '-0.46',
            },
            quote: {
              symbol: 'SZ:000001',
              currency: 'CNY',
              lastPrice: '10.750',
              changePct: '-1.80',
              provider: 'fixture',
              sourceTime: '2026-11-20T06:29:00Z',
              fetchedAt: '2026-11-20T06:30:00Z',
              isStale: false,
            },
          },
          {
            securityId: 'sec-3',
            strategyId: 'default',
            market: 'SH',
            symbol: '601318',
            name: '中国平安',
            currency: 'CNY',
            remainingQty: '200.0000',
            remainingCost: '9800.00',
            averageCost: '49.000',
            unrealizedPnl: null,
            unrealizedReturnPct: null,
            strategyBreakevenPrice: '49.100',
            needsRefresh: true,
            lowestPriceGroup: null,
            quote: null,
          },
          {
            securityId: 'sec-1',
            strategyId: 'default',
            market: 'SH',
            symbol: '600519',
            name: '贵州茅台',
            currency: 'CNY',
            remainingQty: '20.0000',
            remainingCost: '29200.00',
            averageCost: '1460.000',
            unrealizedPnl: '3800.00',
            unrealizedReturnPct: '13.01',
            strategyBreakevenPrice: '1462.500',
            needsRefresh: false,
            lowestPriceGroup: {
              purchasePrice: '1420.000',
              lotCount: 2,
              remainingQty: '10.0000',
              remainingCost: '14200.00',
              unrealizedPnl: '2300.00',
              unrealizedReturnPct: '16.20',
            },
            quote: {
              symbol: 'SH:600519',
              currency: 'CNY',
              lastPrice: '1650.000',
              changePct: '3.25',
              provider: 'fixture',
              sourceTime: '2026-11-20T06:29:00Z',
              fetchedAt: '2026-11-20T06:30:00Z',
              isStale: false,
            },
          },
        ],
        quotes: [
          {
            symbol: 'SH:600519',
            currency: 'CNY',
            lastPrice: '1650.000',
            changePct: '3.25',
            provider: 'fixture',
            sourceTime: '2026-11-20T06:29:00Z',
            fetchedAt: '2026-11-20T06:30:00Z',
            isStale: false,
          },
          {
            symbol: 'SZ:000001',
            currency: 'CNY',
            lastPrice: '10.750',
            changePct: '-1.80',
            provider: 'fixture',
            sourceTime: '2026-11-20T06:29:00Z',
            fetchedAt: '2026-11-20T06:30:00Z',
            isStale: false,
          },
        ],
      })
    }

    if (path.endsWith('/intraday')) {
      return respond(route, {
        symbol: 'SH:600519',
        currency: 'CNY',
        tradingDate: '2026-11-20',
        points: [
          { time: '2026-11-20T01:30:00Z', price: '1610.000' },
          { time: '2026-11-20T02:30:00Z', price: '1635.000' },
          { time: '2026-11-20T06:30:00Z', price: '1650.000' },
        ],
        quote: {
          symbol: 'SH:600519',
          currency: 'CNY',
          lastPrice: '1650.000',
          changePct: '3.25',
          provider: 'fixture',
          sourceTime: '2026-11-20T06:29:00Z',
          fetchedAt: '2026-11-20T06:30:00Z',
          isStale: false,
        },
        provider: 'fixture',
        fetchedAt: '2026-11-20T06:30:00Z',
        isStale: false,
        error: null,
      })
    }

    if (path.endsWith('/lots')) {
      const includeClosed = url.searchParams.get('includeClosed') === 'true'
      return respond(route, {
        accountId: 'acc-cny',
        strategyId: 'default',
        currency: 'CNY',
        closedLotCount: 1,
        needsRefresh: false,
        quote: {
          symbol: 'SH:600519',
          currency: 'CNY',
          lastPrice: '1650.000',
          changePct: '3.25',
          provider: 'fixture',
          sourceTime: '2026-11-20T06:29:00Z',
          fetchedAt: '2026-11-20T06:30:00Z',
          isStale: false,
        },
        summary: {
          remainingQty: '20.0000',
          remainingCost: '29200.00',
          realizedPnl: '1250.00',
          unrealizedPnl: '3800.00',
        },
        lots: [
          {
            id: 'lot-open-001',
            buyTradeId: 'trade-buy-1',
            accountId: 'acc-cny',
            securityId: 'sec-1',
            strategyId: 'default',
            cycleId: 'cycle-1',
            originalQty: '20.0000',
            originalCost: '29200.00',
            remainingQty: '20.0000',
            remainingCost: '29200.00',
            unitCost: '1460.000',
            executedAt: '2026-11-18T02:00:00Z',
            status: 'OPEN',
            unrealizedPnl: '3800.00',
            unrealizedReturnPct: '13.01',
            effectiveTarget: {
              source: 'GLOBAL',
              targetReturnPct: '0.15',
              targetPriceEstimate: '1679.000',
              calculationBasis: 'NET',
              alertEnabled: true,
            },
            targets: [],
          },
          ...(includeClosed
            ? [
                {
                  id: 'lot-closed-002',
                  buyTradeId: 'trade-buy-0',
                  accountId: 'acc-cny',
                  securityId: 'sec-1',
                  strategyId: 'default',
                  cycleId: 'cycle-1',
                  originalQty: '10.0000',
                  originalCost: '14000.00',
                  remainingQty: '0.0000',
                  remainingCost: '0.00',
                  unitCost: '1400.000',
                  executedAt: '2026-11-10T02:00:00Z',
                  status: 'CLOSED',
                  targets: [],
                },
              ]
            : []),
        ],
        closedTradeDetails: includeClosed
          ? [
              {
                lotId: 'lot-closed-002',
                tradeId: 'trade-sell-0',
                side: 'SELL',
                executedAt: '2026-11-15T05:00:00Z',
                qty: '10.0000',
                originalTradeQty: '10.0000',
                price: '1530.000',
                cashAmount: '15250.00',
                realizedPnl: '1250.00',
              },
              {
                lotId: 'lot-closed-002',
                tradeId: 'trade-buy-0',
                side: 'BUY',
                executedAt: '2026-11-10T02:00:00Z',
                qty: '10.0000',
                originalTradeQty: '10.0000',
                price: '1398.000',
                cashAmount: '14000.00',
                realizedPnl: null,
              },
            ]
          : [],
      })
    }

    if (path === '/trades/buy' && req.method() === 'POST') {
      state.buyCount += 1
      return respond(route, { id: 'trade-new-buy' })
    }

    if (path === '/trades/sell/preview' && req.method() === 'POST') {
      return respond(route, {
        currency: 'CNY',
        qty: '10',
        price: '1660.000',
        executedAt: '2026-11-20T06:30:00Z',
        cashIn: '16580.00',
        realizedPnl: '1980.00',
        matches: [
          {
            lotId: 'lot-open-001',
            buyExecutedAt: '2026-11-18T02:00:00Z',
            buyPrice: '1460.000',
            buyQty: '20.0000',
            availableQty: '20.0000',
            matchedQty: '10.0000',
            allocatedBuyCost: '14600.00',
          },
        ],
      })
    }

    if (path === '/trades/sell' && req.method() === 'POST') {
      state.sellCount += 1
      return respond(route, { id: 'trade-new-sell' })
    }

    if (path === '/trades' && req.method() === 'GET') {
      return respond(route, [
        {
          id: 'trade-buy-1',
          accountId: 'acc-cny',
          securityId: 'sec-1',
          strategyId: 'default',
          market: 'SH',
          symbol: '600519',
          name: '贵州茅台',
          side: 'BUY',
          qty: '20.0000',
          price: '1458.500000',
          commission: '5.00',
          stampDuty: '0.00',
          transferFee: '0.30',
          otherFee: '0.00',
          feesTotal: '5.30',
          executedAt: '2026-11-18T02:00:00Z',
          status: 'ACTIVE',
          revision: 1,
          memo: '移动端测试买入',
        },
      ])
    }

    if (path.startsWith('/trades/') && req.method() === 'PATCH') {
      state.editSaved += 1
      return respond(route, {
        trade: {
          id: 'trade-buy-1',
          revision: 2,
        },
      })
    }

    if (path.includes('/profit-settings')) {
      if (req.method() === 'PUT') {
        state.targetSaved += 1
        return respond(route, { targetReturnPct: '0.15', calculationBasis: 'NET', alertEnabled: true })
      }
      return respond(route, { targetReturnPct: '0.15', calculationBasis: 'NET', alertEnabled: true })
    }

    if (path.includes('/targets') && (req.method() === 'POST' || req.method() === 'PUT')) {
      state.targetSaved += 1
      return respond(route, { id: 'target-1' })
    }

    if (path === '/analytics/summary') {
      return respond(route, {
        totalBuyCashOut: '48000.00',
        totalSellCashIn: '6200.00',
        realizedPnl: '1250.00',
        remainingCost: '43050.00',
      })
    }

    if (path === '/cycles') {
      return respond(route, [
        {
          id: 'cycle-1',
          securityId: 'SH:600519',
          strategyId: 'default',
          status: 'OPEN',
          openedAt: '2026-11-10T02:00:00Z',
          closedAt: null,
        },
      ])
    }

    return respond(route, [])
  })

  return state
}

test('登录页在 360x780 手机视口下无横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 })
  await mockMobileApi(page, false)
  await page.goto('/login')
  await expect(page.getByText('邮箱验证码登录')).toBeVisible()
  await expect(page.getByRole('button', { name: '获取验证码' })).toBeVisible()
  await assertNoHorizontalOverflow(page)
})

for (const viewport of [
  { width: 390, height: 844, label: '390x844' },
  { width: 360, height: 780, label: '360x780' },
]) {
  test(`移动端高密度总览、排序、快速记账与核心页面在 ${viewport.label} 零横向溢出`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    const state = await mockMobileApi(page, true)

    await page.goto('/dashboard')
    await expect(page.locator('.m-topbar')).toBeVisible()
    await expect(page.locator('.m-tabbar')).toBeVisible()
    await expect(page.locator('.ap-list .ap-row')).toHaveCount(3)

    // 验证活动仓位默认按当前涨幅降序，无行情持仓始终在末尾，且三列展示现价/低批/上卖
    const links = page.locator('.ap-row .ap-code-link')
    await expect(links).toHaveText(['SH:600519', 'SZ:000001', 'SH:601318'])
    await expect(page.locator('.ap-row').first()).toContainText('低批')
    await expect(page.locator('.ap-row').first()).toContainText('上卖')
    await expect(page.locator('.ap-row').first()).not.toContainText('回本')
    await expect(page.locator('.ap-row').first().getByText('实时', { exact: true })).toHaveCount(0)
    await assertNoHorizontalOverflow(page)

    // 点击现价排序头切换升序，无行情持仓仍固定在末尾
    await page.locator('.ap-th-prices .ap-sort-btn', { hasText: '现价' }).click()
    await expect(links).toHaveText(['SZ:000001', 'SH:600519', 'SH:601318'])

    // 全局止盈弹窗无横向溢出
    await page.getByRole('button', { name: '全局止盈' }).click()
    await expect(page.getByRole('dialog', { name: '全局止盈设置' })).toBeVisible()
    await assertNoHorizontalOverflow(page)
    await page.getByRole('button', { name: '取消' }).click()

    // 快速记账：查找证券 -> 买入记账 -> 卖出配对预览与确认
    await page.locator('.m-quick-row input').fill('600519')
    await page.locator('.m-quick-row').getByRole('button', { name: '查找' }).click()
    await page.locator('.m-security-option').first().click()
    await expect(page.locator('.m-selected-box')).toContainText('SH:600519')

    await page.locator('.m-selected-actions').getByRole('button', { name: '买入记账' }).click()
    const buyDialog = page.getByRole('dialog', { name: /录入买入成交/ })
    await expect(buyDialog).toBeVisible()
    await assertNoHorizontalOverflow(page)
    await buyDialog.locator('input').nth(0).fill('10')
    await buyDialog.locator('input').nth(1).fill('1650')
    await buyDialog.getByRole('button', { name: '保存买入' }).click()
    await expect.poll(() => state.buyCount).toBe(1)

    await page.locator('.m-selected-actions').getByRole('button', { name: '卖出配对' }).click()
    const sellDrawer = page.getByRole('dialog', { name: /卖出配对工作台/ })
    await expect(sellDrawer).toBeVisible()
    await sellDrawer.locator('input').nth(0).fill('10')
    await sellDrawer.locator('input').nth(1).fill('1660')
    await sellDrawer.getByRole('button', { name: '预览最近优先等量配对' }).click()
    await expect(sellDrawer.locator('.m-match-list .m-match-item')).toHaveCount(1)
    await assertNoHorizontalOverflow(page)
    await sellDrawer.getByRole('button', { name: '确认卖出' }).click()
    await expect.poll(() => state.sellCount).toBe(1)

    // 进入个股详情页验证上方常驻活动仓位横向切换条、默认收起的操作栏/分时图、5项汇总（含上次卖出价）、持仓批次、止盈弹窗与已结清明细
    await page.locator('.ap-code-link', { hasText: 'SH:600519' }).click()
    await expect(page).toHaveURL(/\/security\/SH\/600519/)
    await expect(page.locator('.m-pos-strip .m-pos-chip')).toHaveCount(3)
    await expect(page.locator('.m-pos-strip .m-pos-chip.is-active')).toContainText('贵州茅台')
    await expect(page.locator('.m-detail-actions')).toHaveCount(0)
    await expect(page.locator('.m-chart-body')).toHaveCount(0)
    await page.locator('.m-collapse-btn').click()
    await expect(page.locator('.m-detail-actions')).toBeVisible()
    await page.locator('.m-collapse-head').click()
    await expect(page.getByRole('img', { name: '当前交易日股价走势图' })).toBeVisible()
    await expect(page.locator('.m-holding-summary .summary-item')).toHaveCount(5)
    await expect(page.locator('.m-lot-list .m-lot-item')).toHaveCount(1)
    await assertNoHorizontalOverflow(page)

    // 点击横向活动仓位条切换个股再切回
    await page.locator('.m-pos-strip .m-pos-chip', { hasText: '平安银行' }).click()
    await expect(page).toHaveURL(/\/security\/SZ\/000001/)
    await expect(page.locator('.m-pos-strip .m-pos-chip.is-active')).toContainText('平安银行')
    await page.locator('.m-pos-strip .m-pos-chip', { hasText: '贵州茅台' }).click()
    await expect(page).toHaveURL(/\/security\/SH\/600519/)

    await page.locator('.m-lot-item').getByRole('button', { name: '设置' }).click()
    await expect(page.getByRole('dialog', { name: '设置批次止盈目标' })).toBeVisible()
    await assertNoHorizontalOverflow(page)
    await page.getByRole('button', { name: '取消' }).click()

    await page.locator('.m-lot-tools .el-switch').click()
    await expect(page.locator('.m-settled-list .m-settled-item')).toHaveCount(2)
    await assertNoHorizontalOverflow(page)

    // 通过底部 TabBar 切换到交易流水页并打开编辑弹窗
    await page.locator('.m-tabbar').getByRole('link', { name: /流水/ }).click()
    await expect(page).toHaveURL(/\/trades/)
    await expect(page.locator('.m-trade-list .m-trade-item')).toHaveCount(1)
    await assertNoHorizontalOverflow(page)

    await page.locator('.m-trade-item').getByRole('button', { name: '编辑' }).click()
    await expect(page.getByRole('dialog', { name: '编辑交易流水' })).toBeVisible()
    await assertNoHorizontalOverflow(page)
    await page.getByRole('button', { name: '取消' }).click()

    // 通过底部 TabBar 切换到复盘分析页
    await page.locator('.m-tabbar').getByRole('link', { name: /复盘/ }).click()
    await expect(page).toHaveURL(/\/analytics/)
    await expect(page.locator('.m-analytics-grid .m-analytics-cell')).toHaveCount(4)
    await expect(page.locator('.m-cycle-list .m-cycle-item')).toHaveCount(1)
    await assertNoHorizontalOverflow(page)
  })
}
