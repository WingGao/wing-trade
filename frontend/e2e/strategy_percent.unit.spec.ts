import { expect, test } from '@playwright/test'
import { formatSignedPercent, trendClass } from '../src/utils/format'
import {
  calcBuyVsCurrentPercent,
  calcRelativePercent,
  calcSellVsBuyPercent,
} from '../src/utils/strategyPercent'

test.describe('strategyPercent 策略网格价格百分比计算', () => {
  test('买入价与现价百分比：低于现价为负，高于现价为正，相等为零', () => {
    const lower = calcBuyVsCurrentPercent('9.50', 10)
    expect(lower).not.toBeNull()
    expect(formatSignedPercent(lower)).toBe('-5.00%')
    expect(trendClass(lower)).toBe('down')

    const higher = calcBuyVsCurrentPercent('10.50', 10)
    expect(higher).not.toBeNull()
    expect(formatSignedPercent(higher)).toBe('+5.00%')
    expect(trendClass(higher)).toBe('up')

    const equal = calcBuyVsCurrentPercent('10.00', 10)
    expect(equal).toBe('0')
    expect(formatSignedPercent(equal)).toBe('0.00%')
    expect(trendClass(equal)).toBe('neutral')
  })

  test('卖出价与买入价百分比：高于买入价为正，低于买入价为负，支持基金四位净值', () => {
    const gain = calcSellVsBuyPercent('11.50', '10.00')
    expect(gain).not.toBeNull()
    expect(formatSignedPercent(gain)).toBe('+15.00%')
    expect(trendClass(gain)).toBe('up')

    const loss = calcSellVsBuyPercent('9.20', '10.00')
    expect(loss).not.toBeNull()
    expect(formatSignedPercent(loss)).toBe('-8.00%')
    expect(trendClass(loss)).toBe('down')

    const fundGain = calcSellVsBuyPercent('1.2345', '1.1234')
    expect(fundGain).not.toBeNull()
    expect(formatSignedPercent(fundGain)).toBe('+9.89%')
  })

  test('缺失或非正价格时安全返回 null，不产生 NaN 或 Infinity', () => {
    expect(calcBuyVsCurrentPercent(null, 10)).toBeNull()
    expect(calcBuyVsCurrentPercent('9.50', null)).toBeNull()
    expect(calcBuyVsCurrentPercent('9.50', 0)).toBeNull()
    expect(calcBuyVsCurrentPercent('-1', 10)).toBeNull()
    expect(calcSellVsBuyPercent('12.00', null)).toBeNull()
    expect(calcSellVsBuyPercent(null, '10.00')).toBeNull()
    expect(calcRelativePercent('abc', '10')).toBeNull()
  })
})
