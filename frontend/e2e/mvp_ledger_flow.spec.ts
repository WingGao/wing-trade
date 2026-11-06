import { test, expect } from '@playwright/test'

test.describe('portfolio journal MVP shell', () => {
  test('navigates between the main workspaces', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByRole('heading', { name: '总览', exact: true })).toBeVisible()
    await page.getByText('交易流水 Trades').click()
    await expect(page.getByRole('heading', { name: '交易流水' })).toBeVisible()
    await page.getByText('复盘分析 Analytics').click()
    await expect(page.getByRole('heading', { name: '复盘分析' })).toBeVisible()
  })

  test('shows offline quote state without blocking the workspace', async ({ page }) => {
    await page.route('**/api/v1/**', async (route) => {
      if (route.request().url().includes('/quotes')) return route.fulfill({ status: 200, body: JSON.stringify({ quotes: [] }) })
      await route.fulfill({ status: 200, body: JSON.stringify([]) })
    })
    await page.goto('/dashboard')
    await expect(page.getByText('暂无自选股行情')).toBeVisible()
  })

  test('exposes quick buy and sell actions after selecting a security', async ({ page }) => {
    await page.route('**/api/v1/positions**', (route) => route.fulfill({ status: 200, body: '[]' }))
    await page.route('**/api/v1/analytics/summary**', (route) => route.fulfill({ status: 200, body: JSON.stringify({ totalBuyCashOut: '0.0000', totalSellCashIn: '0.0000', realizedPnl: '0.0000', remainingQty: '0.0000', remainingCost: '0.0000' }) }))
    await page.route('**/api/v1/quotes**', (route) => route.fulfill({ status: 200, body: JSON.stringify({ quotes: [] }) }))
    await page.route('**/api/v1/securities?query=600519', (route) => route.fulfill({ status: 200, body: JSON.stringify([{ id: 'security-1', market: 'SH', symbol: '600519', name: '贵州茅台' }]) }))
    await page.route('**/api/v1/securities/security-1/lots**', (route) => route.fulfill({ status: 200, body: JSON.stringify({ lots: [{ id: 'lot-1', status: 'OPEN' }], closedLotCount: 0, realizedPnl: '0.0000' }) }))

    await page.goto('/dashboard')
    await page.getByPlaceholder('输入证券代码、名称，例如 600519').fill('600519')
    await page.getByRole('button', { name: '查找证券' }).click()
    await page.getByRole('button', { name: /SH:600519/ }).click()
    await expect(page.getByRole('button', { name: '买入记账' })).toBeVisible()
    await expect(page.getByRole('button', { name: '卖出配对' })).toBeVisible()
  })
})
