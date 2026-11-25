/**
 * 基于 BigInt 的前端精确十进制派生估值与分时合并工具。
 * 严格避免 JavaScript 二进制浮点运算造成的金额与收益率漂移；
 * 账务事实（持股数、剩余成本、平均成本、已实现收益、待刷新状态）始终以服务端为准。
 */
import type { DashboardPosition, DashboardRead, IntradayRead, Lot, LotsSummary, Quote, ValuationStatus } from '../api/types'

interface DecimalValue {
  digits: bigint
  scale: number
}

const TEN = 10n

/** 计算 10 的非负整数次幂（BigInt）。 */
function pow10(exponent: number): bigint {
  return TEN ** BigInt(exponent)
}

/** 将十进制字符串或整数解析为精确的 (digits, scale) 结构，非法值返回 null。 */
export function parseDecimal(raw: string | number | null | undefined): DecimalValue | null {
  if (raw === null || raw === undefined) return null
  const text = String(raw).trim()
  if (!text) return null
  const match = /^([+-]?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(text)
  if (!match) return null
  const sign = match[1] === '-' ? -1n : 1n
  const intPart = match[2] ?? '0'
  const fracPart = match[3] ?? ''
  const exp = match[4] ? Number.parseInt(match[4], 10) : 0
  if (!Number.isFinite(exp) || Math.abs(exp) > 100) return null
  const rawDigits = BigInt(intPart + fracPart)
  let scale = fracPart.length - exp
  let digits = rawDigits * sign
  if (scale < 0) {
    digits *= pow10(-scale)
    scale = 0
  }
  return { digits, scale }
}

/** 判断数值字符串是否为严格大于 0 的有效十进制数。 */
export function isPositiveDecimal(raw: string | number | null | undefined): boolean {
  const parsed = parseDecimal(raw)
  return parsed !== null && parsed.digits > 0n
}

/** 对齐两个十进制数的标度并执行精确加法。 */
export function addDecimal(a: DecimalValue, b: DecimalValue): DecimalValue {
  if (a.scale === b.scale) return { digits: a.digits + b.digits, scale: a.scale }
  if (a.scale > b.scale) {
    return { digits: a.digits + b.digits * pow10(a.scale - b.scale), scale: a.scale }
  }
  return { digits: a.digits * pow10(b.scale - a.scale) + b.digits, scale: b.scale }
}

/** 执行精确减法 (a - b)。 */
export function subDecimal(a: DecimalValue, b: DecimalValue): DecimalValue {
  return addDecimal(a, { digits: -b.digits, scale: b.scale })
}

/** 执行精确乘法 (a * b)。 */
export function mulDecimal(a: DecimalValue, b: DecimalValue): DecimalValue {
  return { digits: a.digits * b.digits, scale: a.scale + b.scale }
}

/** 对 BigInt 商执行对称四舍五入（ROUND_HALF_UP），与 Python Decimal 保持一致。 */
function divRoundHalfUp(numerator: bigint, denominator: bigint): bigint {
  if (denominator === 0n) throw new Error('division by zero')
  const negative = (numerator < 0n) !== (denominator < 0n)
  const absNum = numerator < 0n ? -numerator : numerator
  const absDen = denominator < 0n ? -denominator : denominator
  const quotient = absNum / absDen
  const remainder = absNum % absDen
  const rounded = remainder * 2n >= absDen ? quotient + 1n : quotient
  return negative ? -rounded : rounded
}

/** 将精确十进制数按 ROUND_HALF_UP 舍入并格式化为固定小数位字符串。 */
export function formatFixedDecimal(value: DecimalValue, targetScale: number): string {
  let scaledDigits: bigint
  if (value.scale === targetScale) {
    scaledDigits = value.digits
  } else if (value.scale < targetScale) {
    scaledDigits = value.digits * pow10(targetScale - value.scale)
  } else {
    scaledDigits = divRoundHalfUp(value.digits, pow10(value.scale - targetScale))
  }
  const negative = scaledDigits < 0n
  const absDigits = negative ? -scaledDigits : scaledDigits
  const text = absDigits.toString().padStart(targetScale + 1, '0')
  const signPrefix = negative && absDigits !== 0n ? '-' : ''
  if (targetScale === 0) return `${signPrefix}${text}`
  const splitIndex = text.length - targetScale
  return `${signPrefix}${text.slice(0, splitIndex)}.${text.slice(splitIndex)}`
}

/** 计算收益率百分数 (pnl / cost * 100)，成本不大于 0 时返回 null。 */
export function computeReturnPct(pnl: DecimalValue | null, cost: DecimalValue | null): string | null {
  if (!pnl || !cost || cost.digits <= 0n) return null
  // (pnl.digits / 10^pnl.scale) / (cost.digits / 10^cost.scale) * 100
  // 目标保留 6 位小数，对应额外放大 10^8
  const numerator = pnl.digits * pow10(cost.scale + 8)
  const denominator = cost.digits * pow10(pnl.scale)
  const scaled = divRoundHalfUp(numerator, denominator)
  return formatFixedDecimal({ digits: scaled, scale: 6 }, 6)
}

/** 构造规范大写证券代码（如 SH:600519）。 */
export function canonicalSymbolKey(market?: string | null, symbol?: string | null): string | null {
  const m = (market ?? '').trim().toUpperCase()
  const s = (symbol ?? '').trim().toUpperCase()
  if (!m || !s) return null
  return `${m}:${s}`
}

/** 比较基础快照与实时推流快照的新鲜度，优先采信更新且有效的报价。 */
export function pickNewerQuote(base: Quote | null | undefined, live: Quote | undefined): Quote | null {
  if (!live || !isPositiveDecimal(live.lastPrice)) return base ?? null
  if (!base || !isPositiveDecimal(base.lastPrice)) {
    return {
      ...live,
      securityId: live.securityId ?? base?.securityId ?? null,
      market: live.market ?? base?.market ?? null,
      currency: live.currency ?? base?.currency ?? null,
    }
  }
  const baseMs = base.fetchedAt ? Date.parse(base.fetchedAt) : Number.NaN
  const liveMs = live.fetchedAt ? Date.parse(live.fetchedAt) : Number.NaN
  if (Number.isFinite(baseMs) && Number.isFinite(liveMs) && liveMs < baseMs) {
    return base
  }
  return {
    ...live,
    securityId: live.securityId ?? base.securityId ?? null,
    market: live.market ?? base.market ?? null,
    currency: live.currency ?? base.currency ?? null,
  }
}

/**
 * 将实时推流行情投影到后端返回的 DashboardRead 基准快照上：
 * 只重算仓位浮盈、最低价组浮盈、自选报价及账户级汇总市值/浮盈/覆盖状态，
 * 绝不修改持股数、剩余成本、平均成本、已实现收益和待刷新标记。
 */
export function projectDashboardWithQuotes(
  base: DashboardRead,
  quotesBySymbol: ReadonlyMap<string, Quote>,
): DashboardRead {
  if (!quotesBySymbol.size) return base

  let latestAsOfMs = Date.parse(base.asOf)
  let latestAsOf = base.asOf
  let anyChanged = false

  const projectedPositions: DashboardPosition[] = base.positions.map((pos) => {
    const key = canonicalSymbolKey(pos.market, pos.symbol) ?? pos.quote?.symbol?.toUpperCase() ?? null
    const live = key ? quotesBySymbol.get(key) : undefined
    const mergedQuote = pickNewerQuote(pos.quote, live)
    if (mergedQuote === pos.quote) return pos

    anyChanged = true
    if (mergedQuote?.fetchedAt) {
      const ms = Date.parse(mergedQuote.fetchedAt)
      if (Number.isFinite(ms) && (!Number.isFinite(latestAsOfMs) || ms > latestAsOfMs)) {
        latestAsOfMs = ms
        latestAsOf = mergedQuote.fetchedAt
      }
    }

    const qty = parseDecimal(pos.remainingQty)
    const cost = parseDecimal(pos.remainingCost)
    const price = parseDecimal(mergedQuote?.lastPrice)
    const hasOpenQty = qty !== null && qty.digits > 0n
    const pnlDec = hasOpenQty && cost !== null && price !== null && price.digits > 0n
      ? subDecimal(mulDecimal(qty, price), cost)
      : null

    let lowestGroup = pos.lowestPriceGroup
    if (lowestGroup && price !== null && price.digits > 0n) {
      const gQty = parseDecimal(lowestGroup.remainingQty)
      const gCost = parseDecimal(lowestGroup.remainingCost)
      if (gQty !== null && gQty.digits > 0n && gCost !== null) {
        const gPnl = subDecimal(mulDecimal(gQty, price), gCost)
        lowestGroup = {
          ...lowestGroup,
          unrealizedPnl: formatFixedDecimal(gPnl, 4),
          unrealizedReturnPct: computeReturnPct(gPnl, gCost),
        }
      }
    }

    return {
      ...pos,
      quote: mergedQuote,
      unrealizedPnl: pnlDec ? formatFixedDecimal(pnlDec, 4) : null,
      unrealizedReturnPct: computeReturnPct(pnlDec, cost),
      lowestPriceGroup: lowestGroup,
    }
  })

  const projectedQuotes: Quote[] = base.quotes.map((quote) => {
    const live = quotesBySymbol.get(quote.symbol.toUpperCase())
    const merged = pickNewerQuote(quote, live)
    if (merged !== quote) anyChanged = true
    return merged ?? quote
  })

  if (!anyChanged) return base

  // 严格复刻后端账户级 valuationStatus 与汇总规则
  const openPositions = projectedPositions.filter((pos) => isPositiveDecimal(pos.remainingQty))
  const totalOpenPositions = base.summary.totalOpenPositions || openPositions.length
  let quotedOpenPositions = 0
  let staleOpenPositions = 0
  let totalUnrealized: DecimalValue = { digits: 0n, scale: 4 }

  for (const pos of openPositions) {
    if (!pos.quote || !isPositiveDecimal(pos.quote.lastPrice) || !pos.unrealizedPnl) continue
    quotedOpenPositions += 1
    if (pos.quote.isStale) staleOpenPositions += 1
    const pnl = parseDecimal(pos.unrealizedPnl)
    if (pnl) totalUnrealized = addDecimal(totalUnrealized, pnl)
  }

  const remainingCost = parseDecimal(base.summary.remainingCost) ?? { digits: 0n, scale: 4 }
  let valuationStatus: ValuationStatus
  let summaryUnrealized: string | null
  let summaryMarketValue: string | null
  let summaryReturnPct: string | null

  if (totalOpenPositions === 0) {
    valuationStatus = 'empty'
    summaryUnrealized = '0.0000'
    summaryMarketValue = formatFixedDecimal(remainingCost, 4)
    summaryReturnPct = null
  } else if (quotedOpenPositions === 0) {
    valuationStatus = 'unavailable'
    summaryUnrealized = null
    summaryMarketValue = null
    summaryReturnPct = null
  } else if (quotedOpenPositions < totalOpenPositions) {
    valuationStatus = 'partial'
    summaryUnrealized = null
    summaryMarketValue = null
    summaryReturnPct = null
  } else {
    valuationStatus = staleOpenPositions > 0 ? 'stale' : 'fresh'
    summaryUnrealized = formatFixedDecimal(totalUnrealized, 4)
    summaryMarketValue = formatFixedDecimal(addDecimal(remainingCost, totalUnrealized), 4)
    summaryReturnPct = computeReturnPct(totalUnrealized, remainingCost)
  }

  return {
    ...base,
    asOf: latestAsOf,
    positions: projectedPositions,
    quotes: projectedQuotes,
    summary: {
      ...base.summary,
      valuationStatus,
      totalOpenPositions,
      quotedOpenPositions,
      staleOpenPositions,
      unrealizedPnl: summaryUnrealized,
      marketValue: summaryMarketValue,
      unrealizedReturnPct: summaryReturnPct,
    },
  }
}

/**
 * 将实时报价投影到详情页批次列表与持仓汇总上：
 * 仅重算 OPEN 且有剩余数量的批次浮盈及总浮盈，已结清批次与账务事实保持不变。
 */
export function projectLotsWithQuote(
  lots: Lot[],
  summary: LotsSummary | null,
  quote: Quote | null,
): { lots: Lot[]; summary: LotsSummary | null } {
  const price = parseDecimal(quote?.lastPrice)
  if (!quote || !price || price.digits <= 0n) {
    return { lots, summary }
  }

  let totalOpenUnrealized: DecimalValue = { digits: 0n, scale: 4 }
  let hasOpenQty = false

  const projectedLots = lots.map((lot) => {
    if (lot.status !== 'OPEN') return lot
    const qty = parseDecimal(lot.remainingQty)
    const cost = parseDecimal(lot.remainingCost)
    if (!qty || qty.digits <= 0n || !cost) return lot
    hasOpenQty = true
    const pnl = subDecimal(mulDecimal(qty, price), cost)
    totalOpenUnrealized = addDecimal(totalOpenUnrealized, pnl)
    return {
      ...lot,
      unrealizedPnl: formatFixedDecimal(pnl, 4),
      unrealizedReturnPct: computeReturnPct(pnl, cost),
    }
  })

  if (!summary) return { lots: projectedLots, summary: null }
  const summaryQty = parseDecimal(summary.remainingQty)
  const summaryCost = parseDecimal(summary.remainingCost)
  let nextSummaryUnrealized = summary.unrealizedPnl
  if (!summaryQty || summaryQty.digits <= 0n) {
    nextSummaryUnrealized = null
  } else if (hasOpenQty) {
    nextSummaryUnrealized = formatFixedDecimal(totalOpenUnrealized, 4)
  } else if (summaryCost) {
    nextSummaryUnrealized = formatFixedDecimal(subDecimal(mulDecimal(summaryQty, price), summaryCost), 4)
  }

  return {
    lots: projectedLots,
    summary: {
      ...summary,
      unrealizedPnl: nextSummaryUnrealized,
    },
  }
}

/**
 * 将实时推流报价合并到日内分时曲线（按分钟桶替换末点或追加新点）：
 * 仅对非 nav 曲线且有效正价格生效，迟到旧包不会导致时间倒退。
 */
export function mergeQuoteIntoIntraday(base: IntradayRead, quote: Quote | null): IntradayRead {
  if (!quote || base.seriesKind === 'nav' || !isPositiveDecimal(quote.lastPrice)) {
    return base
  }
  const mergedQuote = pickNewerQuote(base.quote, quote)
  if (!mergedQuote || mergedQuote === base.quote || !mergedQuote.lastPrice) {
    return base
  }
  const tickTime = mergedQuote.sourceTime ?? mergedQuote.fetchedAt
  if (!tickTime) {
    return {
      ...base,
      quote: mergedQuote,
      fetchedAt: mergedQuote.fetchedAt ?? base.fetchedAt,
      isStale: mergedQuote.isStale,
    }
  }
  const tickMs = Date.parse(tickTime)
  if (!Number.isFinite(tickMs)) {
    return {
      ...base,
      quote: mergedQuote,
      fetchedAt: mergedQuote.fetchedAt ?? base.fetchedAt,
      isStale: mergedQuote.isStale,
    }
  }

  const nextPoints = [...base.points]
  const tickMinute = Math.floor(tickMs / 60000)
  const lastPoint = nextPoints[nextPoints.length - 1]
  if (!lastPoint) {
    nextPoints.push({ time: tickTime, price: mergedQuote.lastPrice })
  } else {
    const lastMs = Date.parse(lastPoint.time)
    const lastMinute = Number.isFinite(lastMs) ? Math.floor(lastMs / 60000) : -1
    if (tickMinute === lastMinute) {
      nextPoints[nextPoints.length - 1] = { time: tickTime, price: mergedQuote.lastPrice }
    } else if (tickMinute > lastMinute) {
      nextPoints.push({ time: tickTime, price: mergedQuote.lastPrice })
    }
  }

  return {
    ...base,
    points: nextPoints,
    quote: mergedQuote,
    fetchedAt: mergedQuote.fetchedAt ?? base.fetchedAt,
    isStale: mergedQuote.isStale,
  }
}
