/** 将原始价格解析为有效正数，空值或非正数返回 null。 */
function parsePositivePrice(raw: string | number | null | undefined): number | null {
  if (raw === null || raw === undefined || raw === '') return null
  const parsed = typeof raw === 'number' ? raw : Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

/** 计算目标价相对基准价的涨跌百分比字符串，公式为 (target - base) / base * 100。 */
export function calcRelativePercent(
  targetPrice: string | number | null | undefined,
  basePrice: string | number | null | undefined,
): string | null {
  const target = parsePositivePrice(targetPrice)
  const base = parsePositivePrice(basePrice)
  if (target === null || base === null) return null
  const pct = ((target - base) / base) * 100
  if (!Number.isFinite(pct)) return null
  // 避免浮点极小负数被格式化为 -0.00%
  if (Math.abs(pct) < 1e-9) return '0'
  return pct.toFixed(6)
}

/** 计算策略买入价相对当前现价的百分比（负值表示买入价低于现价，需回调触达）。 */
export function calcBuyVsCurrentPercent(
  buyPrice: string | number | null | undefined,
  currentPrice: string | number | null | undefined,
): string | null {
  return calcRelativePercent(buyPrice, currentPrice)
}

/** 计算策略卖出价相对策略买入价的百分比（正值表示该档网格的预期价差涨幅）。 */
export function calcSellVsBuyPercent(
  sellPrice: string | number | null | undefined,
  buyPrice: string | number | null | undefined,
): string | null {
  return calcRelativePercent(sellPrice, buyPrice)
}
