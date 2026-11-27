import { expect, test } from '@playwright/test'
import type { Lot, SellPreviewMatch } from '../src/api/types'
import {
  buildManualSelectionsFromMatches,
  buildManualSelectionsFromPreview,
  filterSellableLots,
  formatQtyScaled,
  isLotAfterSellTime,
  normalizeQtyString,
  parseQtyScaled,
  serializeManualSelectionsKey,
  summarizeManualSelections,
  validateAndBuildManualMatches,
} from '../src/utils/manualSellMatch'

/** 构造测试用持仓批次对象。 */
function makeLot(
  id: string,
  remainingQty: string,
  executedAt: string,
  status = 'OPEN',
  purchasePrice: string | null = '10.000000',
): Lot {
  return {
    id,
    buyTradeId: `trade-${id}`,
    accountId: 'acc-1',
    securityId: 'sec-1',
    strategyId: 'strat-1',
    cycleId: 'cycle-1',
    executedAt,
    originalQty: remainingQty,
    originalCost: '1000.0000',
    purchasePrice,
    unitCost: '10.050000',
    remainingQty,
    remainingCost: '1000.0000',
    status,
    targets: [],
  }
}

test.describe('manualSellMatch 手动关联卖出纯逻辑', () => {
  test('数量定点数解析、格式化与尾零规范化保持 4 位精度无浮点误差', () => {
    expect(parseQtyScaled('0.1')).toBe(1000n)
    expect(parseQtyScaled('0.2')).toBe(2000n)
    expect(formatQtyScaled(1000n + 2000n)).toBe('0.3')
    expect(parseQtyScaled('12.3456')).toBe(123456n)
    expect(parseQtyScaled('12.34567')).toBeNull()
    expect(parseQtyScaled('abc')).toBeNull()
    expect(normalizeQtyString('200.0000')).toBe('200')
    expect(normalizeQtyString('12.3400')).toBe('12.34')
  })

  test('filterSellableLots 仅保留 OPEN 且可用量大于 0 的批次并按时间倒序排列', () => {
    const lots = [
      makeLot('lot-old', '100.0000', '2026-11-01T09:30:00+08:00'),
      makeLot('lot-closed', '100.0000', '2026-11-05T09:30:00+08:00', 'CLOSED'),
      makeLot('lot-zero', '0.0000', '2026-11-04T09:30:00+08:00', 'OPEN'),
      makeLot('lot-new', '200.0000', '2026-11-03T14:00:00+08:00'),
    ]
    const sellable = filterSellableLots(lots)
    expect(sellable.map((item) => item.id)).toEqual(['lot-new', 'lot-old'])
  })

  test('汇总与校验支持精确小数相加（0.1 + 0.2 = 0.3）并拦截非法场景', () => {
    const sellable = filterSellableLots([
      makeLot('lot-a', '0.5000', '2026-11-01T09:30:00+08:00'),
      makeLot('lot-b', '0.5000', '2026-11-02T09:30:00+08:00'),
    ])
    const selections = {
      'lot-a': { checked: true, qty: '0.1' },
      'lot-b': { checked: true, qty: '0.2' },
    }
    const summary = summarizeManualSelections(sellable, selections, '0.3')
    expect(summary.selectedCount).toBe(2)
    expect(summary.allocatedQty).toBe('0.3')
    expect(summary.diffQty).toBe('0')
    expect(summary.isExactMatch).toBe(true)

    const valid = validateAndBuildManualMatches(sellable, selections, '0.3', true)
    expect(valid.ok).toBe(true)
    if (valid.ok) {
      expect(valid.matches).toEqual([
        { lotId: 'lot-b', matchedQty: '0.2' },
        { lotId: 'lot-a', matchedQty: '0.1' },
      ])
    }

    // A 股不允许小数时应拦截
    const cnDecimal = validateAndBuildManualMatches(sellable, selections, '0.3', false)
    expect(cnDecimal.ok).toBe(false)

    // 总量不一致拦截
    const mismatch = validateAndBuildManualMatches(sellable, selections, '0.4', true)
    expect(mismatch.ok).toBe(false)

    // 超可用量拦截
    const exceed = validateAndBuildManualMatches(
      sellable,
      { 'lot-a': { checked: true, qty: '0.6' } },
      '0.6',
      true,
    )
    expect(exceed.ok).toBe(false)

    // 未选批次拦截
    const empty = validateAndBuildManualMatches(sellable, {}, '0.3', true)
    expect(empty.ok).toBe(false)
  })

  test('从预选列表与自动预览转换可正确预填并截断超量', () => {
    const sellable = filterSellableLots([
      makeLot('lot-1', '100.0000', '2026-11-01T09:30:00+08:00'),
      makeLot('lot-2', '200.0000', '2026-11-02T09:30:00+08:00'),
    ])
    const fromInitial = buildManualSelectionsFromMatches(sellable, [
      { lotId: 'lot-1', matchedQty: '150.0000' },
      { lotId: 'lot-missing', matchedQty: '50' },
    ])
    expect(fromInitial).toEqual({
      'lot-1': { checked: true, qty: '100' },
    })

    const previewMatches: SellPreviewMatch[] = [
      {
        lotId: 'lot-2',
        buyTradeId: 'trade-lot-2',
        buyExecutedAt: '2026-11-02T09:30:00+08:00',
        buyPrice: '10.000000',
        buyQty: '200.0000',
        availableQty: '200.0000',
        matchedQty: '75.0000',
        allocatedBuyCost: '750.0000',
        allocatedSellNet: '800.0000',
        realizedPnl: '50.0000',
        matchingMethod: 'EQUAL',
      },
    ]
    const fromPreview = buildManualSelectionsFromPreview(sellable, previewMatches)
    expect(fromPreview).toEqual({
      'lot-2': { checked: true, qty: '75' },
    })
  })

  test('未来批次时间检测与选择指纹序列化准确', () => {
    expect(isLotAfterSellTime('2026-11-05T10:00:00+08:00', '2026-11-04T15:00:00+08:00')).toBe(true)
    expect(isLotAfterSellTime('2026-11-04T10:00:00+08:00', '2026-11-04T15:00:00+08:00')).toBe(false)

    const sellable = filterSellableLots([
      makeLot('lot-1', '100.0000', '2026-11-01T09:30:00+08:00'),
      makeLot('lot-2', '200.0000', '2026-11-02T09:30:00+08:00'),
    ])
    const keyA = serializeManualSelectionsKey(sellable, { 'lot-1': { checked: true, qty: '50' } })
    const keyB = serializeManualSelectionsKey(sellable, { 'lot-1': { checked: true, qty: '60' } })
    expect(keyA).not.toBe(keyB)
  })
})
