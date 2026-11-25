import { expect, test, type Page, type Route } from '@playwright/test'

/** 返回标准 JSON 模拟响应。 */
function respondJson(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

/** 注入可控的 MockEventSource，用于精确验证前端 SSE 推送与零轮询契约。 */
async function installMockEventSource(page: Page, failImmediately = false) {
  await page.addInitScript(({ shouldFail }) => {
    interface ListenerMap {
      [event: string]: Array<(ev: MessageEvent) => void>
    }
    const instances: FakeEventSource[] = []

    class FakeEventSource {
      url: string
      readyState = 0
      onerror: ((ev: Event) => void) | null = null
      private listeners: ListenerMap = {}

      constructor(url: string) {
        this.url = url
        instances.push(this)
        if (shouldFail) {
          queueMicrotask(() => {
            this.readyState = 2
            this.onerror?.(new Event('error'))
          })
        }
      }

      addEventListener(type: string, cb: (ev: MessageEvent) => void) {
        this.listeners[type] = this.listeners[type] ?? []
        this.listeners[type].push(cb)
      }

      removeEventListener(type: string, cb: (ev: MessageEvent) => void) {
        this.listeners[type] = (this.listeners[type] ?? []).filter((item) => item !== cb)
      }

      close() {
        this.readyState = 2
      }

      emit(type: string, payload: unknown) {
        if (this.readyState === 2) return
        this.readyState = 1
        const event = new MessageEvent(type, { data: JSON.stringify(payload) })
        for (const cb of this.listeners[type] ?? []) cb(event)
      }

      triggerError() {
        this.readyState = 2
        this.onerror?.(new Event('error'))
      }
    }

    ;(window as unknown as Record<string, unknown>).EventSource = FakeEventSource
    ;(window as unknown as Record<string, unknown>).__sseInstances = instances
    ;(window as unknown as Record<string, unknown>).__emitSse = (type: string, payload: unknown) => {
      const latest = instances[instances.length - 1]
      latest?.emit(type, payload)
    }
  }, { shouldFail: failImmediately })
}

test('首页接入 SSE 实时推流：零轮询实时重算浮盈市值并维持缺行情不可用契约', async ({ page }) => {
  await page.clock.install()
  await installMockEventSource(page)

  let dashboardRequests = 0
  const adminUser = {
    id: 'admin-1',
    email: 'admin@localhost',
    displayName: '默认管理员',
    role: 'ADMIN',
    status: 'ACTIVE',
    isDefaultAdmin: true,
    createdAt: '2026-11-18T00:00:00Z',
  }

  await page.route('**/api/v1/auth/me', (route) => respondJson(route, { user: adminUser }))
  await page.route('**/api/v1/accounts', (route) =>
    respondJson(route, [{ id: 'acc-1', name: 'A股账户', currency: 'CNY', broker: null }]),
  )
  await page.route('**/api/v1/dashboard**', (route) => {
    dashboardRequests += 1
    return respondJson(route, {
      accountId: 'acc-1',
      currency: 'CNY',
      asOf: '2026-11-25T02:00:00Z',
      marketSessionOpen: true,
      marketSession: 'regular',
      pollAfterSeconds: 10,
      positions: [
        {
          securityId: 'sec-1',
          market: 'SH',
          symbol: '600519',
          name: '贵州茅台',
          securityType: 'STOCK',
          currency: 'CNY',
          strategyId: 'strat-1',
          remainingQty: '10.0000',
          remainingCost: '15000.0000',
          averageCost: '1500.000000',
          realizedPnl: '0.0000',
          unrealizedPnl: '200.0000',
          unrealizedReturnPct: '1.333333',
          strategyBreakevenPrice: '1500.000000',
          fundsRecoveredSurplus: '0.0000',
          quote: {
            symbol: 'SH:600519',
            securityId: 'sec-1',
            market: 'SH',
            currency: 'CNY',
            lastPrice: '1520.000000',
            prevClose: '1500.000000',
            changePct: '1.333333',
            sourceTime: '2026-11-25T02:00:00Z',
            fetchedAt: '2026-11-25T02:00:00Z',
            provider: 'longbridge',
            isStale: false,
            marketSession: 'regular',
          },
          lowestPriceGroup: null,
          needsRefresh: false,
        },
      ],
      pendingRefreshScopes: [],
      summary: {
        currency: 'CNY',
        totalBuyCashOut: '15000.0000',
        totalSellCashIn: '0.0000',
        realizedPnl: '0.0000',
        unrealizedPnl: '200.0000',
        marketValue: '15200.0000',
        unrealizedReturnPct: '1.333333',
        remainingQty: '10.0000',
        remainingCost: '15000.0000',
        valuationStatus: 'fresh',
        totalOpenPositions: 1,
        quotedOpenPositions: 1,
        staleOpenPositions: 0,
      },
      quotes: [],
    })
  })

  await page.goto('/dashboard')
  await expect(page.locator('.dashboard-metrics')).toContainText('¥ 15,200.00')
  await expect(page.locator('.dashboard-metrics')).toContainText('¥ 200.00')

  // 推送首帧 snapshot 使 SSE 进入 live 状态
  await page.evaluate(() => {
    const emit = (window as unknown as { __emitSse: (t: string, p: unknown) => void }).__emitSse
    emit('snapshot', {
      serverTime: '2026-11-25T02:00:01Z',
      provider: 'longbridge',
      streamState: 'live',
      quotes: [],
    })
  })
  await expect(page.getByText('实时推送中')).toBeVisible()

  const requestsAfterLive = dashboardRequests
  // 推送实时新报价：1550.00 => 持仓总价值 15,500.00，账户未实现浮盈 500.00
  await page.evaluate(() => {
    const emit = (window as unknown as { __emitSse: (t: string, p: unknown) => void }).__emitSse
    emit('quote', {
      serverTime: '2026-11-25T02:00:05Z',
      quotes: [
        {
          symbol: 'SH:600519',
          securityId: 'sec-1',
          market: 'SH',
          currency: 'CNY',
          lastPrice: '1550.000000',
          prevClose: '1500.000000',
          changePct: '3.333333',
          sourceTime: '2026-11-25T02:00:05Z',
          fetchedAt: '2026-11-25T02:00:05Z',
          provider: 'longbridge',
          isStale: false,
          marketSession: 'regular',
        },
      ],
    })
  })

  await expect(page.locator('.dashboard-metrics')).toContainText('¥ 15,500.00')
  await expect(page.locator('.dashboard-metrics')).toContainText('¥ 500.00')

  // SSE 处于 live 状态，经过 15 秒不应再发起定时 /dashboard 轮询
  await page.clock.runFor(15000)
  expect(dashboardRequests).toBe(requestsAfterLive)
})

test('详情页接入 SSE 实时推流：关闭 3 秒轮询并实时更新批次浮盈与分时图最新点', async ({ page }) => {
  await page.clock.install()
  await installMockEventSource(page)

  let intradayRequests = 0
  const adminUser = {
    id: 'admin-1',
    email: 'admin@localhost',
    displayName: '默认管理员',
    role: 'ADMIN',
    status: 'ACTIVE',
    isDefaultAdmin: true,
    createdAt: '2026-11-18T00:00:00Z',
  }

  await page.route('**/api/v1/auth/me', (route) => respondJson(route, { user: adminUser }))
  await page.route('**/api/v1/accounts', (route) =>
    respondJson(route, [{ id: 'acc-1', name: 'A股账户', currency: 'CNY', broker: null }]),
  )
  await page.route('**/api/v1/dashboard**', (route) =>
    respondJson(route, {
      accountId: 'acc-1',
      currency: 'CNY',
      asOf: '2026-11-25T02:00:00Z',
      marketSessionOpen: true,
      pollAfterSeconds: 10,
      positions: [],
      summary: {
        currency: 'CNY',
        totalBuyCashOut: '1000.0000',
        totalSellCashIn: '0.0000',
        realizedPnl: '0.0000',
        unrealizedPnl: '0.0000',
        marketValue: '1000.0000',
        unrealizedReturnPct: null,
        remainingQty: '100.0000',
        remainingCost: '1000.0000',
        valuationStatus: 'fresh',
        totalOpenPositions: 1,
        quotedOpenPositions: 1,
        staleOpenPositions: 0,
      },
      quotes: [],
    }),
  )
  await page.route('**/api/v1/securities?**', (route) =>
    respondJson(route, [
      { id: 'sec-1', market: 'SH', symbol: '600519', name: '贵州茅台', securityType: 'STOCK', currency: 'CNY' },
    ]),
  )
  await page.route('**/api/v1/securities/sec-1/lots**', (route) =>
    respondJson(route, {
      lots: [
        {
          id: 'lot-1',
          buyTradeId: 't-1',
          accountId: 'acc-1',
          securityId: 'sec-1',
          strategyId: 'strat-1',
          cycleId: 'cyc-1',
          originalQty: '100.0000',
          originalCost: '1000.0000',
          remainingQty: '100.0000',
          remainingCost: '1000.0000',
          unitCost: '10.000000',
          executedAt: '2026-11-17T02:00:00Z',
          status: 'OPEN',
          unrealizedPnl: '100.0000',
          unrealizedReturnPct: '10.000000',
          effectiveTarget: null,
        },
      ],
      closedLotCount: 0,
      realizedPnl: '0.0000',
      summary: {
        remainingQty: '100.0000',
        remainingCost: '1000.0000',
        realizedPnl: '0.0000',
        unrealizedPnl: '100.0000',
      },
      quote: {
        symbol: 'SH:600519',
        securityId: 'sec-1',
        market: 'SH',
        currency: 'CNY',
        lastPrice: '11.000000',
        prevClose: '10.500000',
        changePct: '4.761905',
        sourceTime: '2026-11-25T02:00:00Z',
        fetchedAt: '2026-11-25T02:00:00Z',
        provider: 'longbridge',
        isStale: false,
      },
      currency: 'CNY',
      accountId: 'acc-1',
      strategyId: 'strat-1',
      needsRefresh: false,
    }),
  )
  await page.route('**/api/v1/securities/sec-1/intraday**', (route) => {
    intradayRequests += 1
    return respondJson(route, {
      symbol: 'SH:600519',
      currency: 'CNY',
      tradingDate: '2026-11-25',
      seriesKind: 'intraday',
      points: [
        { time: '2026-11-25T01:30:00Z', price: '10.800000' },
        { time: '2026-11-25T02:00:00Z', price: '11.000000' },
      ],
      quote: {
        symbol: 'SH:600519',
        securityId: 'sec-1',
        market: 'SH',
        currency: 'CNY',
        lastPrice: '11.000000',
        prevClose: '10.500000',
        changePct: '4.761905',
        sourceTime: '2026-11-25T02:00:00Z',
        fetchedAt: '2026-11-25T02:00:00Z',
        provider: 'longbridge',
        isStale: false,
      },
      provider: 'longbridge',
      fetchedAt: '2026-11-25T02:00:00Z',
      isStale: false,
      error: null,
    })
  })

  await page.goto('/security/SH/600519')
  await expect(page.locator('.summary-item').filter({ hasText: '总浮盈' })).toContainText('¥ 100.00')
  await expect.poll(() => intradayRequests).toBe(1)

  // SSE 进入 live 状态并推送新价格 12.50（下一分钟 02:01），总浮盈应立即变为 250.00
  await page.evaluate(() => {
    const emit = (window as unknown as { __emitSse: (t: string, p: unknown) => void }).__emitSse
    emit('snapshot', {
      serverTime: '2026-11-25T02:00:01Z',
      provider: 'longbridge',
      streamState: 'live',
      quotes: [],
    })
    emit('quote', {
      serverTime: '2026-11-25T02:01:00Z',
      quotes: [
        {
          symbol: 'SH:600519',
          securityId: 'sec-1',
          market: 'SH',
          currency: 'CNY',
          lastPrice: '12.500000',
          prevClose: '10.500000',
          changePct: '19.047619',
          sourceTime: '2026-11-25T02:01:00Z',
          fetchedAt: '2026-11-25T02:01:00Z',
          provider: 'longbridge',
          isStale: false,
        },
      ],
    })
  })

  await expect(page.locator('.summary-item').filter({ hasText: '总浮盈' })).toContainText('¥ 250.00')
  await expect(page.getByText('实时推送')).toBeVisible()

  // SSE live 下经过 7 秒，不应再触发 3 秒 intraday 轮询
  await page.clock.runFor(7000)
  expect(intradayRequests).toBe(1)
})
