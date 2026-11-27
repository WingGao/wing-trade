/**
 * 个股详情页回本卖出测算工具。
 * 在给定模拟卖出价下，组合持有中的盈利批次与亏损批次，
 * 在 [-toleranceRate, +toleranceRate]（默认 ±1%）盈亏率容差内求解回本卖出方案。
 * 支持“优先消高价（深亏优先）”与“优先多卖（浅亏优先）”两种亏损批匹配策略。
 * 纯只读测算逻辑，不修改服务端账务状态。
 */
import type { Lot } from '../api/types'
import { resolveLotBuyPrice } from './lotMerge'

export type BreakevenSellStatus =
  | 'OK'
  | 'NO_OPEN_LOTS'
  | 'INVALID_PRICE'
  | 'NO_LOSS_LOTS'
  | 'NO_PROFIT_LOTS'
  | 'NO_SOLUTION'

/**
 * 亏损批次匹配策略：
 * - HIGHEST_LOSS_FIRST: 优先消高价（亏损批按成本从高到低匹配，优先核销山顶套牢批并降低剩余持仓均价）
 * - MAX_QTY: 优先多卖（亏损批按成本从低到高匹配，最大化可卖总数量与回笼资金）
 */
export type BreakevenSellStrategy = 'HIGHEST_LOSS_FIRST' | 'MAX_QTY'

export interface BreakevenSellOptions {
  /** 当前持仓批次列表（内部仅取 OPEN 且剩余数量大于 0 的批次） */
  lots: Lot[]
  /** 模拟卖出价 */
  price: string | number | null | undefined
  /** 是否允许小数数量（美股与场外基金为 true，A 股为 false） */
  allowDecimalQty: boolean
  /** 非小数市场默认交易步长（A 股默认 100 股） */
  lotStep?: number
  /** 盈亏率容差（小数形式，默认 0.01 代表 ±1%） */
  toleranceRate?: number
  /** 亏损批次匹配策略（默认 MAX_QTY 保持向后兼容） */
  strategy?: BreakevenSellStrategy
}

export interface BreakevenLotAllocation {
  lotId: string
  executedAt: string
  buyPrice: string
  unitCost: string
  remainingQty: string
  sellQty: string
  isPartial: boolean
  side: 'PROFIT' | 'LOSS' | 'FLAT'
  sellAmount: string
  allocatedCost: string
  estimatedPnl: string
  estimatedReturnPct: string
}

export interface BreakevenSellResult {
  status: BreakevenSellStatus
  /** 本次测算采用的亏损批匹配策略 */
  strategy: BreakevenSellStrategy
  price: string | null
  tolerancePct: string
  totalHoldingQty: string
  totalHoldingCost: string
  /** 当前全部持仓在模拟卖出价下的整体浮盈率（百分数字符串） */
  overallReturnPct: string | null
  sellQty: string
  sellRatioPct: string
  sellAmount: string
  allocatedCost: string
  estimatedPnl: string
  estimatedReturnPct: string | null
  profitSellQty: string
  lossSellQty: string
  remainingQtyAfter: string
  remainingCostAfter: string
  remainingAvgCostAfter: string | null
  allocations: BreakevenLotAllocation[]
}

interface NormalizedLot {
  lot: Lot
  buyPrice: string
  remainingQty: number
  remainingCost: number
  unitCost: number
  side: 'PROFIT' | 'LOSS' | 'FLAT'
}

interface CandidateSolution {
  quantities: Map<string, number>
  totalQty: number
  totalCost: number
  totalAmount: number
  returnRate: number
  profitQty: number
  lossQty: number
  lotCount: number
}

const EPS = 1e-9
const QTY_EPS = 1e-7
const DECIMAL_QUANTUM = 0.0001

/** 将数值安全解析为正有限浮点数，非法或非正时返回 null。 */
function parsePositiveNumber(raw: string | number | null | undefined): number | null {
  if (raw === null || raw === undefined) return null
  const text = String(raw).trim()
  if (!text) return null
  const num = Number(text)
  return Number.isFinite(num) && num > 0 ? num : null
}

/** 将数量格式化为固定小数位字符串，避免 -0.0000 展示。 */
function formatFixed(value: number, scale: number): string {
  const normalized = Math.abs(value) < 10 ** -(scale + 2) ? 0 : value
  return normalized.toFixed(scale)
}

/** 计算给定总卖出市值与对应总成本的盈亏率（小数形式）。 */
function calcReturnRate(sellAmount: number, cost: number): number {
  if (cost <= EPS) return 0
  return (sellAmount - cost) / cost
}

/** 计算给定单批数量对应的分摊成本（满仓卖出直接取原剩余成本，避免比例误差）。 */
function calcAllocatedCost(item: NormalizedLot, sellQty: number): number {
  if (Math.abs(sellQty - item.remainingQty) <= QTY_EPS) {
    return item.remainingCost
  }
  return item.unitCost * sellQty
}

/** 将连续数量上限按市场步长向下取整为合法卖出数量（不超过该批剩余数量）。 */
function floorToAllowedQty(
  maxQty: number,
  remainingQty: number,
  allowDecimalQty: boolean,
  lotStep: number,
): number {
  if (maxQty <= QTY_EPS || remainingQty <= QTY_EPS) return 0
  const capped = Math.min(maxQty, remainingQty)
  if (allowDecimalQty) {
    const units = Math.floor((capped + 1e-12) / DECIMAL_QUANTUM)
    return Math.min(remainingQty, Number((units * DECIMAL_QUANTUM).toFixed(4)))
  }
  // A 股等整数手市场：若整批可全部卖出（含零股）则允许整批；否则按 lotStep 向下取整手
  if (capped + QTY_EPS >= remainingQty) {
    return remainingQty
  }
  if (remainingQty < lotStep) {
    return 0
  }
  return Math.floor((capped + QTY_EPS) / lotStep) * lotStep
}

/** 返回单批在离散步长约束下所有可选卖出数量（降序排列，用于局部微调）。 */
function listDiscreteOptionsDesc(remainingQty: number, lotStep: number): number[] {
  const options: number[] = []
  if (remainingQty <= QTY_EPS) return [0]
  options.push(remainingQty)
  const fullSteps = Math.floor((remainingQty - QTY_EPS) / lotStep)
  for (let k = fullSteps; k >= 0; k -= 1) {
    const q = k * lotStep
    if (Math.abs(q - remainingQty) > QTY_EPS) {
      options.push(q)
    }
  }
  return options
}

/** 根据各批次卖出数量映射构造并评估候选解，不满足回本约束时返回 null。 */
function evaluateCandidate(
  quantities: Map<string, number>,
  lotsById: Map<string, NormalizedLot>,
  price: number,
  toleranceRate: number,
): CandidateSolution | null {
  let totalQty = 0
  let totalCost = 0
  let profitQty = 0
  let lossQty = 0
  let lotCount = 0

  for (const [lotId, qty] of quantities.entries()) {
    if (qty <= QTY_EPS) continue
    const item = lotsById.get(lotId)
    if (!item || qty > item.remainingQty + QTY_EPS) return null
    const clampedQty = Math.min(qty, item.remainingQty)
    totalQty += clampedQty
    totalCost += calcAllocatedCost(item, clampedQty)
    lotCount += 1
    if (item.side === 'PROFIT') profitQty += clampedQty
    else if (item.side === 'LOSS') lossQty += clampedQty
  }

  if (totalQty <= QTY_EPS || profitQty <= QTY_EPS || lossQty <= QTY_EPS || totalCost <= EPS) {
    return null
  }

  const totalAmount = price * totalQty
  const returnRate = calcReturnRate(totalAmount, totalCost)
  if (Math.abs(returnRate) > toleranceRate + 1e-7) {
    return null
  }

  return {
    quantities: new Map(
      Array.from(quantities.entries()).filter(([, q]) => q > QTY_EPS),
    ),
    totalQty,
    totalCost,
    totalAmount,
    returnRate,
    profitQty,
    lossQty,
    lotCount,
  }
}

/**
 * 比较两个合法候选解，返回更优者。
 * - 若传入 priorityLossLotIds（优先消高价策略），先按高价亏损批序字典序最大化核销数量；
 * - 随后按总数量大优先 → 更接近 0% 优先 → 批次数少优先。
 */
function pickBetterSolution(
  current: CandidateSolution | null,
  candidate: CandidateSolution | null,
  priorityLossLotIds?: string[],
): CandidateSolution | null {
  if (!candidate) return current
  if (!current) return candidate
  if (priorityLossLotIds?.length) {
    for (const lotId of priorityLossLotIds) {
      const candQty = candidate.quantities.get(lotId) ?? 0
      const currQty = current.quantities.get(lotId) ?? 0
      if (candQty > currQty + QTY_EPS) return candidate
      if (currQty > candQty + QTY_EPS) return current
    }
  }
  if (candidate.totalQty > current.totalQty + QTY_EPS) return candidate
  if (current.totalQty > candidate.totalQty + QTY_EPS) return current
  if (Math.abs(candidate.returnRate) + 1e-7 < Math.abs(current.returnRate)) return candidate
  if (Math.abs(current.returnRate) + 1e-7 < Math.abs(candidate.returnRate)) return current
  return candidate.lotCount < current.lotCount ? candidate : current
}

/** 在指定已选数量基础上，尝试按单边批次顺序贪心追加数量至指定盈亏率边界。 */
function greedyFillSide(
  baseQuantities: Map<string, number>,
  baseQty: number,
  baseCost: number,
  sideLots: NormalizedLot[],
  mode: 'FILL_LOSS' | 'FILL_PROFIT',
  price: number,
  toleranceRate: number,
  allowDecimalQty: boolean,
  lotStep: number,
): { quantities: Map<string, number>; criticalLot: NormalizedLot | null } {
  const quantities = new Map(baseQuantities)
  let curQty = baseQty
  let curCost = baseCost
  let criticalLot: NormalizedLot | null = null

  const maxUnitCostBound = price / (1 - toleranceRate)
  const minUnitCostBound = price / (1 + toleranceRate)

  for (const item of sideLots) {
    const nextQty = curQty + item.remainingQty
    const nextCost = curCost + item.remainingCost
    const nextRate = calcReturnRate(price * nextQty, nextCost)
    const withinLimit = mode === 'FILL_LOSS'
      ? nextRate >= -toleranceRate - 1e-9
      : nextRate <= toleranceRate + 1e-9

    if (withinLimit) {
      quantities.set(item.lot.id, item.remainingQty)
      curQty = nextQty
      curCost = nextCost
      continue
    }

    if (!criticalLot) criticalLot = item
    // 求解临界批次上满足容差边界的最大连续卖出数量
    let continuousMax = 0
    if (mode === 'FILL_LOSS') {
      const denom = item.unitCost - maxUnitCostBound
      if (denom > EPS) {
        continuousMax = (maxUnitCostBound * curQty - curCost) / denom
      }
    } else {
      const denom = minUnitCostBound - item.unitCost
      if (denom > EPS) {
        continuousMax = (curCost - minUnitCostBound * curQty) / denom
      }
    }

    const allowedQty = floorToAllowedQty(continuousMax, item.remainingQty, allowDecimalQty, lotStep)
    if (allowedQty > QTY_EPS) {
      quantities.set(item.lot.id, allowedQty)
      curQty += allowedQty
      curCost += calcAllocatedCost(item, allowedQty)
    }
  }

  return { quantities, criticalLot }
}

/** 针对 A 股等离散步长，在边界跳过 ±1% 时通过微调对侧批次手数寻找可行最优组合。 */
function searchDiscreteRefinement(
  baseQuantities: Map<string, number>,
  adjustableLots: NormalizedLot[],
  fillLots: NormalizedLot[],
  mode: 'FILL_LOSS' | 'FILL_PROFIT',
  lotsById: Map<string, NormalizedLot>,
  price: number,
  toleranceRate: number,
  lotStep: number,
  priorityLossLotIds?: string[],
): CandidateSolution | null {
  let best: CandidateSolution | null = null

  // 逐一尝试微调对侧的某一条批次数量，再重新贪心填充目标侧，兼顾性能与离散最优性
  for (const targetLot of adjustableLots) {
    const options = listDiscreteOptionsDesc(targetLot.remainingQty, lotStep)
    for (const optQty of options) {
      const trialBase = new Map(baseQuantities)
      if (optQty > QTY_EPS) trialBase.set(targetLot.lot.id, optQty)
      else trialBase.delete(targetLot.lot.id)

      let trialQty = 0
      let trialCost = 0
      for (const [id, q] of trialBase.entries()) {
        const lotItem = lotsById.get(id)
        if (!lotItem || q <= QTY_EPS) continue
        trialQty += q
        trialCost += calcAllocatedCost(lotItem, q)
      }
      if (trialQty <= QTY_EPS) continue

      const { quantities } = greedyFillSide(
        trialBase,
        trialQty,
        trialCost,
        fillLots,
        mode,
        price,
        toleranceRate,
        false,
        lotStep,
      )
      best = pickBetterSolution(
        best,
        evaluateCandidate(quantities, lotsById, price, toleranceRate),
        priorityLossLotIds,
      )
    }
  }

  return best
}

/** 构造无解或异常状态下的标准测算返回对象。 */
function buildEmptyResult(
  status: BreakevenSellStatus,
  strategy: BreakevenSellStrategy,
  price: number | null,
  toleranceRate: number,
  totalHoldingQty: number,
  totalHoldingCost: number,
  overallReturnPct: string | null,
): BreakevenSellResult {
  const avgCostAfter = totalHoldingQty > QTY_EPS ? formatFixed(totalHoldingCost / totalHoldingQty, 6) : null
  return {
    status,
    strategy,
    price: price !== null ? formatFixed(price, 6) : null,
    tolerancePct: formatFixed(toleranceRate * 100, 2),
    totalHoldingQty: formatFixed(totalHoldingQty, 4),
    totalHoldingCost: formatFixed(totalHoldingCost, 4),
    overallReturnPct,
    sellQty: '0.0000',
    sellRatioPct: '0.00',
    sellAmount: '0.0000',
    allocatedCost: '0.0000',
    estimatedPnl: '0.0000',
    estimatedReturnPct: null,
    profitSellQty: '0.0000',
    lossSellQty: '0.0000',
    remainingQtyAfter: formatFixed(totalHoldingQty, 4),
    remainingCostAfter: formatFixed(totalHoldingCost, 4),
    remainingAvgCostAfter: avgCostAfter,
    allocations: [],
  }
}

/**
 * 基于当前模拟卖出价测算“盈利批 + 亏损批”组合回本卖出方案。
 * 在 [-toleranceRate, +toleranceRate] 容差内，按所选策略（优先消高价 / 优先多卖）求解最优组合，
 * 同优先级下优先选择组合盈亏率最接近 0% 的方案。
 */
export function calculateBreakevenSell(options: BreakevenSellOptions): BreakevenSellResult {
  const toleranceRate = options.toleranceRate ?? 0.01
  const lotStep = options.lotStep ?? 100
  const allowDecimalQty = options.allowDecimalQty
  const strategy: BreakevenSellStrategy = options.strategy ?? 'MAX_QTY'

  const openLots = options.lots
    .filter((lot) => lot.status === 'OPEN')
    .map((lot) => {
      const remainingQty = parsePositiveNumber(lot.remainingQty)
      const remainingCost = parsePositiveNumber(lot.remainingCost)
      if (!remainingQty || !remainingCost) return null
      return {
        lot,
        buyPrice: resolveLotBuyPrice(lot),
        remainingQty,
        remainingCost,
        unitCost: remainingCost / remainingQty,
        side: 'FLAT' as 'PROFIT' | 'LOSS' | 'FLAT',
      }
    })
    .filter((item): item is NormalizedLot => item !== null)

  const totalHoldingQty = openLots.reduce((sum, item) => sum + item.remainingQty, 0)
  const totalHoldingCost = openLots.reduce((sum, item) => sum + item.remainingCost, 0)

  if (!openLots.length || totalHoldingQty <= QTY_EPS) {
    return buildEmptyResult('NO_OPEN_LOTS', strategy, null, toleranceRate, 0, 0, null)
  }

  const price = parsePositiveNumber(options.price)
  if (price === null) {
    return buildEmptyResult('INVALID_PRICE', strategy, null, toleranceRate, totalHoldingQty, totalHoldingCost, null)
  }

  const overallReturnRate = calcReturnRate(price * totalHoldingQty, totalHoldingCost)
  const overallReturnPct = formatFixed(overallReturnRate * 100, 2)

  const profitLots: NormalizedLot[] = []
  const lossLots: NormalizedLot[] = []
  const flatLots: NormalizedLot[] = []
  const lotsById = new Map<string, NormalizedLot>()

  for (const item of openLots) {
    const diff = price - item.unitCost
    if (diff > EPS) {
      item.side = 'PROFIT'
      profitLots.push(item)
    } else if (diff < -EPS) {
      item.side = 'LOSS'
      lossLots.push(item)
    } else {
      item.side = 'FLAT'
      flatLots.push(item)
    }
    lotsById.set(item.lot.id, item)
  }

  if (!lossLots.length) {
    return buildEmptyResult('NO_LOSS_LOTS', strategy, price, toleranceRate, totalHoldingQty, totalHoldingCost, overallReturnPct)
  }
  if (!profitLots.length) {
    return buildEmptyResult('NO_PROFIT_LOTS', strategy, price, toleranceRate, totalHoldingQty, totalHoldingCost, overallReturnPct)
  }

  let best: CandidateSolution | null = null

  // 1. 先检查全仓合计是否已经落在 ±toleranceRate 容差内
  const allMap = new Map<string, number>(openLots.map((item) => [item.lot.id, item.remainingQty]))
  best = pickBetterSolution(best, evaluateCandidate(allMap, lotsById, price, toleranceRate))

  // 2. 若全仓未直接命中，则按偏亏/偏盈方向执行贪心求解
  if (!best) {
    if (overallReturnRate < -toleranceRate) {
      // 全仓偏亏：全取盈利批与平本批，亏损批按所选策略排序填充
      // HIGHEST_LOSS_FIRST 按成本降序（最高价优先）；MAX_QTY 按成本升序（浅亏优先）
      const lossOrdered = strategy === 'HIGHEST_LOSS_FIRST'
        ? [...lossLots].sort((a, b) => b.unitCost - a.unitCost)
        : [...lossLots].sort((a, b) => a.unitCost - b.unitCost)
      const priorityLossLotIds = strategy === 'HIGHEST_LOSS_FIRST'
        ? lossOrdered.map((item) => item.lot.id)
        : undefined
      const baseMap = new Map<string, number>()
      let baseQty = 0
      let baseCost = 0
      for (const item of [...profitLots, ...flatLots]) {
        baseMap.set(item.lot.id, item.remainingQty)
        baseQty += item.remainingQty
        baseCost += item.remainingCost
      }

      const { quantities } = greedyFillSide(
        baseMap,
        baseQty,
        baseCost,
        lossOrdered,
        'FILL_LOSS',
        price,
        toleranceRate,
        allowDecimalQty,
        lotStep,
      )
      best = pickBetterSolution(
        best,
        evaluateCandidate(quantities, lotsById, price, toleranceRate),
        priorityLossLotIds,
      )

      // 离散步长下若主路径被 ±1% 区间夹空或有更优组合，尝试微调盈利批手数
      if (!allowDecimalQty) {
        const profitByImpact = [...profitLots].sort((a, b) => a.unitCost - b.unitCost)
        best = pickBetterSolution(
          best,
          searchDiscreteRefinement(
            baseMap,
            profitByImpact,
            lossOrdered,
            'FILL_LOSS',
            lotsById,
            price,
            toleranceRate,
            lotStep,
            priorityLossLotIds,
          ),
          priorityLossLotIds,
        )
      }
    } else {
      // 全仓偏盈：全取亏损批与平本批，盈利批按单位成本从高到低（浅盈优先）填充
      const profitDesc = [...profitLots].sort((a, b) => b.unitCost - a.unitCost)
      const baseMap = new Map<string, number>()
      let baseQty = 0
      let baseCost = 0
      for (const item of [...lossLots, ...flatLots]) {
        baseMap.set(item.lot.id, item.remainingQty)
        baseQty += item.remainingQty
        baseCost += item.remainingCost
      }

      const { quantities } = greedyFillSide(
        baseMap,
        baseQty,
        baseCost,
        profitDesc,
        'FILL_PROFIT',
        price,
        toleranceRate,
        allowDecimalQty,
        lotStep,
      )
      best = pickBetterSolution(best, evaluateCandidate(quantities, lotsById, price, toleranceRate))

      if (!allowDecimalQty) {
        const lossByImpact = [...lossLots].sort((a, b) => b.unitCost - a.unitCost)
        best = pickBetterSolution(
          best,
          searchDiscreteRefinement(
            baseMap,
            lossByImpact,
            profitDesc,
            'FILL_PROFIT',
            lotsById,
            price,
            toleranceRate,
            lotStep,
          ),
        )
      }
    }
  }

  if (!best) {
    return buildEmptyResult('NO_SOLUTION', strategy, price, toleranceRate, totalHoldingQty, totalHoldingCost, overallReturnPct)
  }

  // 盈利批在前（按成本升序），平本批居中，亏损批在后（按所选策略对应的成本顺序排列）
  const sideOrder: Record<'PROFIT' | 'FLAT' | 'LOSS', number> = { PROFIT: 0, FLAT: 1, LOSS: 2 }
  const allocations: BreakevenLotAllocation[] = Array.from(best.quantities.entries())
    .map(([lotId, qty]) => {
      const item = lotsById.get(lotId)!
      const sellAmount = price * qty
      const allocatedCost = calcAllocatedCost(item, qty)
      const pnl = sellAmount - allocatedCost
      const retPct = calcReturnRate(sellAmount, allocatedCost) * 100
      return {
        lotId: item.lot.id,
        executedAt: item.lot.executedAt,
        buyPrice: item.buyPrice,
        unitCost: formatFixed(item.unitCost, 6),
        remainingQty: formatFixed(item.remainingQty, 4),
        sellQty: formatFixed(qty, 4),
        isPartial: Math.abs(qty - item.remainingQty) > QTY_EPS,
        side: item.side,
        sellAmount: formatFixed(sellAmount, 4),
        allocatedCost: formatFixed(allocatedCost, 4),
        estimatedPnl: formatFixed(pnl, 4),
        estimatedReturnPct: formatFixed(retPct, 2),
      }
    })
    .sort((a, b) => {
      if (sideOrder[a.side] !== sideOrder[b.side]) return sideOrder[a.side] - sideOrder[b.side]
      if (a.side === 'LOSS' && strategy === 'HIGHEST_LOSS_FIRST') {
        return Number(b.unitCost) - Number(a.unitCost)
      }
      return Number(a.unitCost) - Number(b.unitCost)
    })

  const remainingQtyAfter = Math.max(0, totalHoldingQty - best.totalQty)
  const remainingCostAfter = Math.max(0, totalHoldingCost - best.totalCost)
  const remainingAvgCostAfter = remainingQtyAfter > QTY_EPS
    ? formatFixed(remainingCostAfter / remainingQtyAfter, 6)
    : null
  const estimatedPnl = best.totalAmount - best.totalCost
  const sellRatioPct = totalHoldingQty > QTY_EPS ? (best.totalQty / totalHoldingQty) * 100 : 0

  return {
    status: 'OK',
    strategy,
    price: formatFixed(price, 6),
    tolerancePct: formatFixed(toleranceRate * 100, 2),
    totalHoldingQty: formatFixed(totalHoldingQty, 4),
    totalHoldingCost: formatFixed(totalHoldingCost, 4),
    overallReturnPct,
    sellQty: formatFixed(best.totalQty, 4),
    sellRatioPct: formatFixed(sellRatioPct, 2),
    sellAmount: formatFixed(best.totalAmount, 4),
    allocatedCost: formatFixed(best.totalCost, 4),
    estimatedPnl: formatFixed(estimatedPnl, 4),
    estimatedReturnPct: formatFixed(best.returnRate * 100, 2),
    profitSellQty: formatFixed(best.profitQty, 4),
    lossSellQty: formatFixed(best.lossQty, 4),
    remainingQtyAfter: formatFixed(remainingQtyAfter, 4),
    remainingCostAfter: formatFixed(remainingCostAfter, 4),
    remainingAvgCostAfter,
    allocations,
  }
}
