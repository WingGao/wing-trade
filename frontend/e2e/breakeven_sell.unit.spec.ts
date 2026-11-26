import { expect, test } from '@playwright/test'
import type { Lot } from '../src/api/types'
import { calculateBreakevenSell } from '../src/utils/breakevenSell'

/** 构造测试用持仓批次对象，简化单测数据准备。 */
function makeLot(
  id: string,
  qty: number,
  unitCost: number,
  status = 'OPEN',
  executedAt = '2026-11-01T09:30:00+08:00',
): Lot {
  const cost = Number((qty * unitCost).toFixed(4))
  return {
    id,
    buyTradeId: `trade-${id}`,
    accountId: 'acc-1',
    securityId: 'sec-1',
    strategyId: 'strat-1',
    cycleId: 'cycle-1',
    executedAt,
    originalQty: qty.toFixed(4),
    originalCost: cost.toFixed(4),
    purchasePrice: unitCost.toFixed(6),
    unitCost: unitCost.toFixed(6),
    remainingQty: status === 'OPEN' ? qty.toFixed(4) : '0.0000',
    remainingCost: status === 'OPEN' ? cost.toFixed(4) : '0.0000',
    status,
    targets: [],
  }
}

test.describe('calculateBreakevenSell 回本卖出测算算法', () => {
  test('全仓偏亏（A股100股步长）：全用盈利批 + 优先浅亏批并支持部分卖出', () => {
    const lots = [
      makeLot('profit-1', 500, 9.0),
      makeLot('loss-shallow', 1000, 10.5),
      makeLot('loss-deep', 1000, 12.0),
    ]
    // 现价 10.0：profit-1 盈利 500 元；loss-shallow 每100股亏50元，在 -1% 容差内可配满 1000 股（甚至还能带部分 loss-deep）
    const res = calculateBreakevenSell({
      lots,
      price: '10.00',
      allowDecimalQty: false,
      lotStep: 100,
    })
    expect(res.status).toBe('OK')
    expect(Number(res.profitSellQty)).toBe(500)
    expect(Number(res.lossSellQty)).toBeGreaterThanOrEqual(1000)
    expect(Number(res.sellQty)).toBeGreaterThanOrEqual(1500)
    const retPct = Number(res.estimatedReturnPct)
    expect(retPct).toBeGreaterThanOrEqual(-1.0)
    expect(retPct).toBeLessThanOrEqual(1.0)
    // A 股每批卖出量应为 100 整数倍
    for (const item of res.allocations) {
      expect(Number(item.sellQty) % 100).toBe(0)
      expect(Number(item.sellQty)).toBeLessThanOrEqual(Number(item.remainingQty))
    }
    // 优先使用浅亏批（loss-shallow 应全部用完）
    const shallow = res.allocations.find((item) => item.lotId === 'loss-shallow')
    expect(shallow).toBeDefined()
    expect(Number(shallow?.sellQty)).toBe(1000)
  })

  test('全仓偏盈（A股100股步长）：全用亏损批 + 优先浅盈批并支持部分卖出', () => {
    const lots = [
      makeLot('profit-deep', 1000, 8.0),
      makeLot('profit-shallow', 1000, 9.5),
      makeLot('loss-1', 500, 11.0),
    ]
    // 现价 10.0：loss-1 亏损 500 元；profit-shallow 每100股盈50元，全卖 1000 股正好 0% 回本
    const res = calculateBreakevenSell({
      lots,
      price: '10.00',
      allowDecimalQty: false,
      lotStep: 100,
    })
    expect(res.status).toBe('OK')
    expect(Number(res.lossSellQty)).toBe(500)
    expect(Number(res.profitSellQty)).toBeGreaterThanOrEqual(1000)
    const retPct = Number(res.estimatedReturnPct)
    expect(retPct).toBeGreaterThanOrEqual(-1.0)
    expect(retPct).toBeLessThanOrEqual(1.0)
    const shallowProfit = res.allocations.find((item) => item.lotId === 'profit-shallow')
    expect(shallowProfit).toBeDefined()
    expect(Number(shallowProfit?.sellQty)).toBe(1000)
  })

  test('全仓本身已在 ±1% 容差内时应直接返回全仓方案', () => {
    const lots = [
      makeLot('p1', 500, 9.5),
      makeLot('l1', 500, 10.5),
    ]
    const res = calculateBreakevenSell({
      lots,
      price: '10.00',
      allowDecimalQty: false,
      lotStep: 100,
    })
    expect(res.status).toBe('OK')
    expect(Number(res.sellQty)).toBe(1000)
    expect(Number(res.remainingQtyAfter)).toBe(0)
    expect(res.remainingAvgCostAfter).toBeNull()
    expect(Number(res.estimatedReturnPct)).toBeCloseTo(0, 2)
  })

  test('美股与场外基金支持最多 4 位小数部分卖出', () => {
    const lots = [
      makeLot('p1', 10, 90),
      makeLot('l1', 20, 115),
    ]
    // 现价 100：p1 盈利 100；l1 每份亏 15，支持小数精确配平至 -1% 边界附近
    const res = calculateBreakevenSell({
      lots,
      price: '100',
      allowDecimalQty: true,
    })
    expect(res.status).toBe('OK')
    expect(Number(res.profitSellQty)).toBe(10)
    const lossAlloc = res.allocations.find((item) => item.lotId === 'l1')
    expect(lossAlloc).toBeDefined()
    expect(lossAlloc?.isPartial).toBe(true)
    expect(Number(lossAlloc?.sellQty)).toBeGreaterThan(6.5)
    expect(Number(lossAlloc?.sellQty)).toBeLessThan(10)
    const retPct = Number(res.estimatedReturnPct)
    expect(retPct).toBeGreaterThanOrEqual(-1.0)
    expect(retPct).toBeLessThanOrEqual(1.0)
  })

  test('A股离散步长触发边界跳过时，局部微调能找到合法回本解', () => {
    // p1 盈利很大（200股成本5元，现价10元，每100股盈500元），l1 亏损200股成本15元（每100股亏500元）
    // 但 l1 只有 100 股，全取 p1(200) + l1(100) 会大盈；减掉 100 股 p1 后 p1(100)+l1(100) 恰好 0%
    const lots = [
      makeLot('p1', 200, 5.0),
      makeLot('l1', 100, 15.0),
    ]
    const res = calculateBreakevenSell({
      lots,
      price: '10.00',
      allowDecimalQty: false,
      lotStep: 100,
    })
    expect(res.status).toBe('OK')
    expect(Number(res.sellQty)).toBe(200)
    expect(Number(res.profitSellQty)).toBe(100)
    expect(Number(res.lossSellQty)).toBe(100)
    expect(Number(res.estimatedReturnPct)).toBeCloseTo(0, 2)
  })

  test('边界状态：全盈利、全亏损、无持仓、非法价格与离散无解', () => {
    const allProfit = calculateBreakevenSell({
      lots: [makeLot('p1', 200, 8.0), makeLot('p2', 300, 9.0)],
      price: '10.00',
      allowDecimalQty: false,
    })
    expect(allProfit.status).toBe('NO_LOSS_LOTS')
    expect(Number(allProfit.overallReturnPct)).toBeGreaterThan(0)

    const allLoss = calculateBreakevenSell({
      lots: [makeLot('l1', 200, 12.0), makeLot('l2', 300, 11.0)],
      price: '10.00',
      allowDecimalQty: false,
    })
    expect(allLoss.status).toBe('NO_PROFIT_LOTS')
    expect(Number(allLoss.overallReturnPct)).toBeLessThan(0)

    const noOpen = calculateBreakevenSell({
      lots: [makeLot('c1', 200, 10.0, 'CLOSED')],
      price: '10.00',
      allowDecimalQty: false,
    })
    expect(noOpen.status).toBe('NO_OPEN_LOTS')

    const badPrice = calculateBreakevenSell({
      lots: [makeLot('p1', 100, 9.0), makeLot('l1', 100, 11.0)],
      price: '0',
      allowDecimalQty: false,
    })
    expect(badPrice.status).toBe('INVALID_PRICE')

    // 离散无解：100股盈500元，100股亏100元，且均无法拆分（步长100），组合收益 (2000-1600)/1600 = +25%
    const noSol = calculateBreakevenSell({
      lots: [makeLot('p1', 100, 5.0), makeLot('l1', 100, 11.0)],
      price: '10.00',
      allowDecimalQty: false,
      lotStep: 100,
    })
    expect(noSol.status).toBe('NO_SOLUTION')
  })
})
