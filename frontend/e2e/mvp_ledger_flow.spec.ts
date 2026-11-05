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
})
