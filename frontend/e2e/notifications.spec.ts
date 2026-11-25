import { expect, test, type Page, type Route } from '@playwright/test'

/** 统一返回模拟 JSON 响应，不触碰真实后端或本地 ledger.db。 */
async function respond(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/** 断言当前视口无页面级横向溢出。 */
async function assertNoHorizontalOverflow(page: Page): Promise<void> {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }))
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.innerWidth)
}

/** 构造用于推送设置弹窗测试的轻量模拟后端。 */
async function mockNotificationApi(page: Page) {
  let config = {
    channelType: 'wecom_webhook',
    enabled: false,
    configured: false,
    maskedWebhookUrl: null as string | null,
    updatedAt: null as string | null,
  }
  const calls = {
    savedPayloads: [] as Array<Record<string, unknown>>,
    testCount: 0,
  }

  await page.route('**/api/v1/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace('/api/v1', '')

    if (path === '/auth/me') {
      return respond(route, {
        user: {
          id: 'user-1',
          email: 'wing@example.com',
          displayName: 'Wing',
          role: 'ADMIN',
          status: 'ACTIVE',
          isDefaultAdmin: true,
          createdAt: '2026-11-25T00:00:00Z',
        },
      })
    }
    if (path === '/accounts') {
      return respond(route, [{ id: 'acc-1', name: 'A股主账户', currency: 'CNY' }])
    }
    if (path === '/securities') {
      return respond(route, [])
    }
    if (path === '/dashboard') {
      return respond(route, {
        accountId: 'acc-1',
        currency: 'CNY',
        asOf: '2026-11-25T02:30:00Z',
        marketSessionOpen: true,
        marketSession: 'regular',
        pollAfterSeconds: 60,
        positions: [],
        quotes: [],
        summary: {
          marketValue: '0.00',
          totalBuyCashOut: '0.00',
          totalSellCashIn: '0.00',
          realizedPnl: '0.00',
          unrealizedPnl: null,
          unrealizedReturnPct: null,
          valuationStatus: 'empty',
          totalOpenPositions: 0,
          quotedOpenPositions: 0,
        },
      })
    }
    if (path === '/quotes/stream') {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
        body: 'event: snapshot\ndata: {"serverTime":"2026-11-25T10:30:00+08:00","provider":"mock","streamState":"mock","quotes":[]}\n\n',
      })
    }
    if (path === '/notifications/wecom' && req.method() === 'GET') {
      return respond(route, config)
    }
    if (path === '/notifications/wecom' && req.method() === 'PUT') {
      const payload = req.postDataJSON() as { enabled: boolean; webhookUrl?: string | null }
      calls.savedPayloads.push(payload)
      if (payload.webhookUrl === null || payload.webhookUrl === '') {
        config = {
          channelType: 'wecom_webhook',
          enabled: false,
          configured: false,
          maskedWebhookUrl: null,
          updatedAt: '2026-11-25T16:40:00+08:00',
        }
      } else if (typeof payload.webhookUrl === 'string') {
        config = {
          channelType: 'wecom_webhook',
          enabled: Boolean(payload.enabled),
          configured: true,
          maskedWebhookUrl: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcd****7890',
          updatedAt: '2026-11-25T16:40:00+08:00',
        }
      } else {
        config = { ...config, enabled: Boolean(payload.enabled) }
      }
      return respond(route, config)
    }
    if (path === '/notifications/wecom/test' && req.method() === 'POST') {
      calls.testCount += 1
      return respond(route, { ok: true, message: '测试消息已发送至企业微信群' })
    }
    return respond(route, {})
  })

  return calls
}

test('桌面端可保存企微推送、脱敏回显、一键测试并清除配置', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  const calls = await mockNotificationApi(page)
  await page.goto('/dashboard')

  await page.getByRole('button', { name: '推送设置' }).click()
  const dialog = page.getByRole('dialog', { name: '止盈推送设置（企业微信）' })
  await expect(dialog).toBeVisible()

  await dialog.locator('.el-switch').click()
  await dialog.getByPlaceholder('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...').fill(
    'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdef1234567890',
  )
  await dialog.getByRole('button', { name: '发送测试' }).click()
  await expect(page.getByText('测试消息已发送至企业微信群')).toBeVisible()
  expect(calls.testCount).toBe(1)

  await dialog.getByRole('button', { name: '保存设置' }).click()
  await expect(dialog).toBeHidden()
  expect(calls.savedPayloads.length).toBe(1)

  // 再次打开确认脱敏回显并可清除配置
  await page.getByRole('button', { name: '推送设置' }).click()
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcd****7890')).toBeVisible()
  await dialog.getByRole('button', { name: '清除配置' }).click()
  await expect(dialog).toBeHidden()
  expect(calls.savedPayloads[1]).toEqual({ enabled: false, webhookUrl: null })
})

test('移动端 390x844 与 360x780 打开推送设置弹窗零横向溢出', async ({ page }) => {
  for (const viewport of [
    { width: 390, height: 844 },
    { width: 360, height: 780 },
  ]) {
    await page.setViewportSize(viewport)
    await mockNotificationApi(page)
    await page.goto('/dashboard')
    await assertNoHorizontalOverflow(page)

    await page.getByRole('button', { name: '推送', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '止盈推送设置（企业微信）' })
    await expect(dialog).toBeVisible()
    await assertNoHorizontalOverflow(page)
    await dialog.getByRole('button', { name: '取消' }).click()
    await expect(dialog).toBeHidden()
  }
})
