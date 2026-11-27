import type { Lot, SellPreviewMatch } from '../api/types'

/** 手动关联或预选的单条批次配对项。 */
export interface ManualSellMatchItem {
  lotId: string
  matchedQty: string
}

/** 卖出配对模式：自动等量配对或手动关联批次。 */
export type SellMatchMode = 'EQUAL' | 'MANUAL'

/** 单个可卖批次在手动模式下的勾选与输入草稿。 */
export interface ManualLotSelection {
  checked: boolean
  qty: string
}

/** 手动关联列表的实时汇总状态。 */
export interface ManualSelectionSummary {
  selectedCount: number
  allocatedQty: string
  allocatedScaled: bigint
  targetQty: string
  targetScaled: bigint | null
  diffScaled: bigint | null
  diffQty: string | null
  isExactMatch: boolean
  hasInvalidEntry: boolean
}

/** 手动关联预览前校验结果。 */
export type ManualMatchesValidationResult =
  | { ok: true; matches: ManualSellMatchItem[] }
  | { ok: false; message: string }

const QTY_SCALE = 4
const QTY_FACTOR = 10_000n

/** 将数量字符串规范化为去除小数末尾冗余零的展示/输入字符串。 */
export function normalizeQtyString(value?: string | null): string {
  const trimmed = (value ?? '').trim()
  if (!trimmed) return ''
  return trimmed.includes('.') ? trimmed.replace(/(\.\d*?[1-9])0+$/, '$1').replace(/\.0+$/, '') : trimmed
}

/** 将十进制数量字符串精确解析为 4 位定点数（BigInt）；非法或超过 4 位小数返回 null。 */
export function parseQtyScaled(raw?: string | null): bigint | null {
  const trimmed = (raw ?? '').trim()
  if (!trimmed) return null
  const match = /^([+-])?(\d+)(?:\.(\d+))?$/.exec(trimmed)
  if (!match) return null
  const sign = match[1] === '-' ? -1n : 1n
  const intPart = match[2] ?? '0'
  const fracPart = match[3] ?? ''
  if (fracPart.length > QTY_SCALE) return null
  const paddedFrac = fracPart.padEnd(QTY_SCALE, '0')
  return sign * (BigInt(intPart) * QTY_FACTOR + BigInt(paddedFrac))
}

/** 将 4 位定点数（BigInt）格式化为去除多余尾零的十进制字符串。 */
export function formatQtyScaled(scaled: bigint): string {
  const negative = scaled < 0n
  const abs = negative ? -scaled : scaled
  const intPart = abs / QTY_FACTOR
  const fracPart = (abs % QTY_FACTOR).toString().padStart(QTY_SCALE, '0').replace(/0+$/, '')
  const body = fracPart ? `${intPart}.${fracPart}` : `${intPart}`
  return negative && body !== '0' ? `-${body}` : body
}

/** 筛选当前可参与手动卖出的未结清批次，按买入时间倒序（同时间按 ID 倒序）排列。 */
export function filterSellableLots(lots: Lot[]): Lot[] {
  return lots
    .filter((lot) => {
      if (lot.status !== 'OPEN') return false
      const scaled = parseQtyScaled(lot.remainingQty)
      return scaled !== null && scaled > 0n
    })
    .sort((a, b) => {
      const timeDiff = toTimestampMs(b.executedAt) - toTimestampMs(a.executedAt)
      if (timeDiff !== 0) return timeDiff
      return b.id.localeCompare(a.id)
    })
}

/** 解析批次或卖出的 ISO 时间戳为毫秒数（无时区按 UTC 解释），解析失败返回 0。 */
export function toTimestampMs(iso?: string | null): number {
  const trimmed = (iso ?? '').trim()
  if (!trimmed) return 0
  const hasZone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(trimmed)
  const parsed = Date.parse(hasZone ? trimmed : `${trimmed}Z`)
  return Number.isFinite(parsed) ? parsed : 0
}

/** 判断某批次的买入时间是否晚于当前填写的卖出成交时间。 */
export function isLotAfterSellTime(lotExecutedAt: string, sellExecutedAtIso: string): boolean {
  if (!sellExecutedAtIso) return false
  const lotMs = toTimestampMs(lotExecutedAt)
  const sellMs = toTimestampMs(sellExecutedAtIso)
  if (!lotMs || !sellMs) return false
  return lotMs > sellMs
}

/** 返回单个批次用于展示的买入参考价（优先原始成交价，缺失时回退含费单位成本）。 */
export function resolveSellLotPrice(lot: Lot): string {
  return lot.purchasePrice ?? lot.unitCost
}

/** 根据初始预选列表或自动预览结果生成手动勾选草稿表（仅保留当前可卖批次并截断至可用量）。 */
export function buildManualSelectionsFromMatches(
  sellableLots: Lot[],
  sourceMatches: ReadonlyArray<Pick<ManualSellMatchItem, 'lotId' | 'matchedQty'>>,
): Record<string, ManualLotSelection> {
  const byId = new Map<string, Lot>()
  for (const lot of sellableLots) {
    byId.set(lot.id, lot)
  }
  const result: Record<string, ManualLotSelection> = {}
  for (const item of sourceMatches) {
    const lot = byId.get(item.lotId)
    if (!lot) continue
    const available = parseQtyScaled(lot.remainingQty)
    const requested = parseQtyScaled(item.matchedQty)
    if (available === null || available <= 0n || requested === null || requested <= 0n) continue
    const clamped = requested > available ? available : requested
    result[lot.id] = {
      checked: true,
      qty: formatQtyScaled(clamped),
    }
  }
  return result
}

/** 从自动配对预览明细直接转换为手动勾选草稿表。 */
export function buildManualSelectionsFromPreview(
  sellableLots: Lot[],
  previewMatches: ReadonlyArray<SellPreviewMatch>,
): Record<string, ManualLotSelection> {
  return buildManualSelectionsFromMatches(
    sellableLots,
    previewMatches.map((item) => ({ lotId: item.lotId, matchedQty: item.matchedQty })),
  )
}

/** 汇总当前手动模式下已选批次数、已分配总量与目标卖出总量的差额状态。 */
export function summarizeManualSelections(
  sellableLots: Lot[],
  selections: Record<string, ManualLotSelection>,
  targetQtyRaw: string,
): ManualSelectionSummary {
  let selectedCount = 0
  let allocatedScaled = 0n
  let hasInvalidEntry = false

  for (const lot of sellableLots) {
    const entry = selections[lot.id]
    if (!entry?.checked) continue
    selectedCount += 1
    const available = parseQtyScaled(lot.remainingQty)
    const parsed = parseQtyScaled(entry.qty)
    if (parsed === null || parsed <= 0n || available === null || parsed > available) {
      hasInvalidEntry = true
      continue
    }
    allocatedScaled += parsed
  }

  const targetScaled = parseQtyScaled(targetQtyRaw)
  const validTarget = targetScaled !== null && targetScaled > 0n ? targetScaled : null
  const diffScaled = validTarget === null ? null : allocatedScaled - validTarget
  const isExactMatch =
    selectedCount > 0 &&
    !hasInvalidEntry &&
    validTarget !== null &&
    diffScaled === 0n

  return {
    selectedCount,
    allocatedQty: formatQtyScaled(allocatedScaled),
    allocatedScaled,
    targetQty: validTarget === null ? '' : formatQtyScaled(validTarget),
    targetScaled: validTarget,
    diffScaled,
    diffQty: diffScaled === null ? null : formatQtyScaled(diffScaled),
    isExactMatch,
    hasInvalidEntry,
  }
}

/** 校验手动关联草稿并构造发送给服务端预览/提交接口的稳定 matches 载荷。 */
export function validateAndBuildManualMatches(
  sellableLots: Lot[],
  selections: Record<string, ManualLotSelection>,
  targetQtyRaw: string,
  allowDecimalQty: boolean,
): ManualMatchesValidationResult {
  const targetScaled = parseQtyScaled(targetQtyRaw)
  if (targetScaled === null || targetScaled <= 0n) {
    return { ok: false, message: '请填写大于 0 的有效卖出数量' }
  }

  const matches: ManualSellMatchItem[] = []
  let allocatedScaled = 0n

  for (const lot of sellableLots) {
    const entry = selections[lot.id]
    if (!entry?.checked) continue
    const parsed = parseQtyScaled(entry.qty)
    if (parsed === null || parsed <= 0n) {
      return { ok: false, message: `批次 ${lot.id.slice(0, 8)} 的配对数量必须大于 0（最多 4 位小数）` }
    }
    if (!allowDecimalQty && parsed % QTY_FACTOR !== 0n) {
      return { ok: false, message: `批次 ${lot.id.slice(0, 8)} 的配对数量须为整数` }
    }
    const available = parseQtyScaled(lot.remainingQty)
    if (available === null || parsed > available) {
      return {
        ok: false,
        message: `批次 ${lot.id.slice(0, 8)} 的配对数量超过当前可用量（可用 ${normalizeQtyString(lot.remainingQty)}）`,
      }
    }
    allocatedScaled += parsed
    matches.push({
      lotId: lot.id,
      matchedQty: formatQtyScaled(parsed),
    })
  }

  if (!matches.length) {
    return { ok: false, message: '请至少选择一个要关联的买入批次' }
  }
  if (allocatedScaled !== targetScaled) {
    return {
      ok: false,
      message: `已分配数量（${formatQtyScaled(allocatedScaled)}）与卖出数量（${formatQtyScaled(targetScaled)}）不一致`,
    }
  }

  return { ok: true, matches }
}

/** 为当前手动勾选状态生成稳定指纹，用于检测输入变化并失效旧预览。 */
export function serializeManualSelectionsKey(
  sellableLots: Lot[],
  selections: Record<string, ManualLotSelection>,
): string {
  const pairs: Array<[string, string]> = []
  for (const lot of sellableLots) {
    const entry = selections[lot.id]
    if (!entry?.checked) continue
    pairs.push([lot.id, entry.qty.trim()])
  }
  return JSON.stringify(pairs)
}
