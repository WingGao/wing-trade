/**
 * 持仓批次按买入价格合并工具。
 * 严格使用 BigInt 精确十进制累加数量、成本与浮盈，仅用于前端分组展示，不篡改服务端账务事实。
 */
import type { EffectiveTarget, Lot, ProfitTarget } from '../api/types'
import { addDecimal, computeReturnPct, formatFixedDecimal, parseDecimal } from './valuation'

export interface LotDisplayRow {
  rowKey: string
  isMerged: boolean
  lotCount: number
  lot: Lot | null
  lots: Lot[]
  children?: LotDisplayRow[]
  id: string
  buyPrice: string
  unitCost: string
  originalQty: string
  originalCost: string
  remainingQty: string
  remainingCost: string
  executedAt: string
  status: string
  unrealizedPnl: string | null
  unrealizedReturnPct: string | null
  targets: ProfitTarget[]
  effectiveTarget: EffectiveTarget | null
  explicitTargetLotCount: number
}

const TEN = 10n

/** 对 BigInt 商执行对称四舍五入（ROUND_HALF_UP）。 */
function divRoundHalfUp(numerator: bigint, denominator: bigint): bigint {
  const negative = (numerator < 0n) !== (denominator < 0n)
  const absNum = numerator < 0n ? -numerator : numerator
  const absDen = denominator < 0n ? -denominator : denominator
  const quotient = absNum / absDen
  const remainder = absNum % absDen
  const rounded = remainder * 2n >= absDen ? quotient + 1n : quotient
  return negative ? -rounded : rounded
}

/** 计算两个精确十进制字符串的商并按 ROUND_HALF_UP 格式化为固定小数位。 */
function divideDecimalFixed(numeratorRaw: string, denominatorRaw: string, targetScale: number): string | null {
  const num = parseDecimal(numeratorRaw)
  const den = parseDecimal(denominatorRaw)
  if (!num || !den || den.digits <= 0n) return null
  const scaledNumerator = num.digits * (TEN ** BigInt(den.scale + targetScale))
  const scaledDenominator = den.digits * (TEN ** BigInt(num.scale))
  const quotient = divRoundHalfUp(scaledNumerator, scaledDenominator)
  return formatFixedDecimal({ digits: quotient, scale: targetScale }, targetScale)
}

/** 将一批精确十进制字符串求和并格式化为固定小数位。 */
function sumDecimalStrings(values: string[], targetScale: number): string {
  let acc = { digits: 0n, scale: targetScale }
  for (const raw of values) {
    const parsed = parseDecimal(raw)
    if (parsed) {
      acc = addDecimal(acc, parsed)
    }
  }
  return formatFixedDecimal(acc, targetScale)
}

/** 提取批次用于分组展示的买入价格，优先使用原始成交价，缺失时回退含费单价。 */
export function resolveLotBuyPrice(lot: Lot): string {
  const candidate = lot.purchasePrice ?? lot.unitCost
  const parsed = parseDecimal(candidate)
  if (!parsed) return candidate.trim()
  return formatFixedDecimal(parsed, 6)
}

/** 判断单条批次是否配置了仍有效的显式分段止盈目标。 */
function hasActiveExplicitTargets(lot: Lot): boolean {
  return (lot.targets ?? []).some((target) => !['CANCELLED', 'COMPLETED'].includes(target.status))
}

/** 将单条批次转换为统一表格展示行。 */
function toSingleLotRow(lot: Lot): LotDisplayRow {
  return {
    rowKey: lot.id,
    isMerged: false,
    lotCount: 1,
    lot,
    lots: [lot],
    id: lot.id,
    buyPrice: resolveLotBuyPrice(lot),
    unitCost: lot.unitCost,
    originalQty: lot.originalQty,
    originalCost: lot.originalCost,
    remainingQty: lot.remainingQty,
    remainingCost: lot.remainingCost,
    executedAt: lot.executedAt,
    status: lot.status,
    unrealizedPnl: lot.unrealizedPnl ?? null,
    unrealizedReturnPct: lot.unrealizedReturnPct ?? null,
    targets: lot.targets ?? [],
    effectiveTarget: lot.effectiveTarget ?? null,
    explicitTargetLotCount: hasActiveExplicitTargets(lot) ? 1 : 0,
  }
}

/** 汇总多条同状态、同买入价批次的继承止盈信息，仅当上级来源与收益率一致时展示。 */
function resolveMergedEffectiveTarget(groupLots: Lot[]): EffectiveTarget | null {
  const inherited = groupLots
    .map((lot) => lot.effectiveTarget)
    .filter((target): target is EffectiveTarget => Boolean(target && target.source !== 'LOT'))
  if (inherited.length !== groupLots.length || inherited.length === 0) return null
  const first = inherited[0]!
  const sameRule = inherited.every(
    (item) => item.source === first.source && item.targetReturnPct === first.targetReturnPct,
  )
  if (!sameRule) return null
  const sameEstimate = inherited.every((item) => item.targetPriceEstimate === first.targetPriceEstimate)
  return {
    ...first,
    targetPriceEstimate: sameEstimate ? first.targetPriceEstimate : null,
  }
}

/** 将同状态且同买入价的多条批次精确合并为父行，并保留原始子批次供展开查看。 */
function toMergedLotRow(groupKey: string, buyPrice: string, groupLots: Lot[]): LotDisplayRow {
  const latest = groupLots[0]!
  const status = latest.status
  const originalQty = sumDecimalStrings(groupLots.map((lot) => lot.originalQty), 4)
  const originalCost = sumDecimalStrings(groupLots.map((lot) => lot.originalCost), 4)
  const remainingQty = sumDecimalStrings(groupLots.map((lot) => lot.remainingQty), 4)
  const remainingCost = sumDecimalStrings(groupLots.map((lot) => lot.remainingCost), 4)
  const unitCost = status === 'OPEN'
    ? divideDecimalFixed(remainingCost, remainingQty, 6) ?? latest.unitCost
    : divideDecimalFixed(originalCost, originalQty, 6) ?? latest.unitCost

  let unrealizedPnl: string | null = null
  let unrealizedReturnPct: string | null = null
  if (status === 'OPEN' && groupLots.every((lot) => lot.unrealizedPnl !== null && lot.unrealizedPnl !== undefined)) {
    unrealizedPnl = sumDecimalStrings(groupLots.map((lot) => lot.unrealizedPnl as string), 4)
    unrealizedReturnPct = computeReturnPct(parseDecimal(unrealizedPnl), parseDecimal(remainingCost))
  }

  const explicitTargetLotCount = groupLots.filter((lot) => hasActiveExplicitTargets(lot)).length
  const effectiveTarget = explicitTargetLotCount === 0 ? resolveMergedEffectiveTarget(groupLots) : null

  return {
    rowKey: `merged:${groupKey}`,
    isMerged: true,
    lotCount: groupLots.length,
    lot: null,
    lots: groupLots,
    children: groupLots.map((lot) => toSingleLotRow(lot)),
    id: `merged:${groupKey}`,
    buyPrice,
    unitCost,
    originalQty,
    originalCost,
    remainingQty,
    remainingCost,
    executedAt: latest.executedAt,
    status,
    unrealizedPnl,
    unrealizedReturnPct,
    targets: [],
    effectiveTarget,
    explicitTargetLotCount,
  }
}

/** 按买入时间倒序整理持仓批次；开启合并时按「状态 + 买入价」精确聚合多笔批次。 */
export function buildLotDisplayRows(lots: Lot[], mergeByPrice: boolean): LotDisplayRow[] {
  const sorted = [...lots].sort(
    (a, b) => new Date(b.executedAt).getTime() - new Date(a.executedAt).getTime() || b.id.localeCompare(a.id),
  )
  if (!mergeByPrice) {
    return sorted.map((lot) => toSingleLotRow(lot))
  }

  const groups = new Map<string, { buyPrice: string; lots: Lot[] }>()
  for (const lot of sorted) {
    const buyPrice = resolveLotBuyPrice(lot)
    const groupKey = `${lot.status}|${buyPrice}`
    const existing = groups.get(groupKey)
    if (existing) {
      existing.lots.push(lot)
    } else {
      groups.set(groupKey, { buyPrice, lots: [lot] })
    }
  }

  const rows: LotDisplayRow[] = []
  for (const [groupKey, group] of groups.entries()) {
    if (group.lots.length === 1) {
      rows.push(toSingleLotRow(group.lots[0]!))
    } else {
      rows.push(toMergedLotRow(groupKey, group.buyPrice, group.lots))
    }
  }
  return rows
}
