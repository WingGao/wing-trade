import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import type {
  DashboardPosition,
  Quote,
  WatchlistItem,
  WatchlistUpdatePayload,
  WatchlistUpsertPayload,
} from '../api/types'
import {
  canonicalSymbolKey,
  computePriceDiffPct,
  parseDecimal,
  pickNewerQuote,
  subDecimal,
} from '../utils/valuation'

/** 将实时 SSE 报价投影到关注项，实时重算距上次卖出、距目标买回价与命中状态。 */
export function projectWatchlistItemWithQuote(
  item: WatchlistItem,
  quotesBySymbol: ReadonlyMap<string, Quote>,
): WatchlistItem {
  const key = canonicalSymbolKey(item.market, item.symbol)
  const liveQuote = key ? quotesBySymbol.get(key) : undefined
  const mergedQuote = pickNewerQuote(item.quote, liveQuote)
  if (mergedQuote === item.quote) {
    return item
  }
  const latestDec = parseDecimal(mergedQuote?.lastPrice)
  const lastSellDec = parseDecimal(item.lastSellPrice)
  const targetDec = parseDecimal(item.targetBuyPrice)
  const vsLastSellPct = computePriceDiffPct(latestDec, lastSellDec)
  const vsTargetBuyPct = computePriceDiffPct(latestDec, targetDec)
  const hitBuyTarget = Boolean(
    latestDec && targetDec && subDecimal(latestDec, targetDec).digits <= 0n,
  )
  return {
    ...item,
    quote: mergedQuote,
    vsLastSellPct,
    vsTargetBuyPct,
    hitBuyTarget,
  }
}

/** 批量将实时行情映射投影到关注列表。 */
export function projectWatchlistWithQuotes(
  items: WatchlistItem[],
  quotesBySymbol: ReadonlyMap<string, Quote>,
): WatchlistItem[] {
  if (!quotesBySymbol.size || !items.length) return items
  return items.map((item) => projectWatchlistItemWithQuote(item, quotesBySymbol))
}

/**
 * 将关注列表按「显示关注」开关混入首页持仓列表：
 * - 已出现在持仓（或已结清）列表中的标的：原行叠加关注星标与目标买回价信息，不重复行；
 * - 未出现在列表中的已关注空仓标的：仅在 showWatchlist=true 时构造成空仓关注行追加。
 */
export function mergePositionsWithWatchlist(
  positions: DashboardPosition[],
  watchlistItems: WatchlistItem[],
  showWatchlist: boolean,
): DashboardPosition[] {
  if (!watchlistItems.length) return positions
  const watchBySecurityId = new Map<string, WatchlistItem>()
  for (const item of watchlistItems) {
    watchBySecurityId.set(item.securityId, item)
  }

  const seenSecurityIds = new Set<string>()
  const enrichedPositions: DashboardPosition[] = positions.map((pos) => {
    seenSecurityIds.add(pos.securityId)
    const watch = watchBySecurityId.get(pos.securityId)
    if (!watch) return pos
    return {
      ...pos,
      isWatchlist: true,
      isWatchlistOnly: false,
      targetBuyPrice: watch.targetBuyPrice ?? null,
      alertEnabled: watch.alertEnabled,
      watchlistNote: watch.note ?? null,
      vsTargetBuyPct: watch.vsTargetBuyPct ?? null,
      hitBuyTarget: watch.hitBuyTarget,
    }
  })

  if (!showWatchlist) {
    return enrichedPositions
  }

  const appended: DashboardPosition[] = []
  for (const item of watchlistItems) {
    if (seenSecurityIds.has(item.securityId)) continue
    appended.push({
      securityId: item.securityId,
      market: item.market,
      symbol: item.symbol,
      name: item.name ?? null,
      securityType: item.securityType ?? null,
      currency: item.currency ?? null,
      strategyId: item.strategyId || `watchlist:${item.securityId}`,
      remainingQty: '0',
      remainingCost: '0',
      averageCost: '0',
      realizedPnl: item.realizedPnl ?? '0',
      unrealizedPnl: null,
      unrealizedReturnPct: null,
      strategyBreakevenPrice: null,
      fundsRecoveredSurplus: null,
      quote: item.quote ?? null,
      lowestPriceGroup: null,
      lastSellPrice: item.lastSellPrice ?? null,
      lastSellExecutedAt: item.lastSellExecutedAt ?? null,
      vsLastSellPct: item.vsLastSellPct ?? null,
      avgAmplitude20d: item.avgAmplitude20d ?? null,
      avgAmplitudeDays: item.avgAmplitudeDays ?? null,
      avgAmplitudeAsOf: item.avgAmplitudeAsOf ?? null,
      needsRefresh: false,
      isWatchlist: true,
      isWatchlistOnly: true,
      targetBuyPrice: item.targetBuyPrice ?? null,
      alertEnabled: item.alertEnabled,
      watchlistNote: item.note ?? null,
      vsTargetBuyPct: item.vsTargetBuyPct ?? null,
      hitBuyTarget: item.hitBuyTarget,
    })
  }
  return [...enrichedPositions, ...appended]
}

export const useWatchlistStore = defineStore('watchlist', () => {
  const items = ref<WatchlistItem[]>([])
  const accountId = ref<string>('')
  const loading = ref(false)
  const error = ref('')
  let requestGeneration = 0

  const bySecurityId = computed(() => {
    const map = new Map<string, WatchlistItem>()
    for (const item of items.value) {
      map.set(item.securityId, item)
    }
    return map
  })

  /** 加载指定账户的关注列表，带代次防并发覆盖。 */
  async function load(targetAccountId?: string): Promise<WatchlistItem[]> {
    const generation = ++requestGeneration
    loading.value = true
    error.value = ''
    try {
      const result = await api.watchlist(targetAccountId)
      if (generation !== requestGeneration) return items.value
      items.value = result
      accountId.value = targetAccountId ?? result[0]?.accountId ?? ''
      return result
    } catch (cause) {
      if (generation === requestGeneration) {
        error.value = cause instanceof Error ? cause.message : '加载关注列表失败'
      }
      return items.value
    } finally {
      if (generation === requestGeneration) {
        loading.value = false
      }
    }
  }

  /** 将最新单项回写或插入本地缓存列表。 */
  function upsertLocal(next: WatchlistItem): void {
    const index = items.value.findIndex((item) => item.securityId === next.securityId)
    if (index >= 0) {
      const copy = [...items.value]
      copy[index] = next
      items.value = copy
    } else {
      items.value = [...items.value, next]
    }
  }

  /** 新增或幂等更新关注证券。 */
  async function add(payload: WatchlistUpsertPayload, targetAccountId?: string): Promise<WatchlistItem> {
    const saved = await api.addWatchlist(payload, targetAccountId)
    upsertLocal(saved)
    return saved
  }

  /** 更新关注证券的目标买回价、提醒开关或备注。 */
  async function update(
    securityId: string,
    payload: WatchlistUpdatePayload,
    targetAccountId?: string,
  ): Promise<WatchlistItem> {
    const saved = await api.updateWatchlist(securityId, payload, targetAccountId)
    upsertLocal(saved)
    return saved
  }

  /** 取消关注指定证券并同步清理本地状态。 */
  async function remove(securityId: string, targetAccountId?: string): Promise<void> {
    await api.removeWatchlist(securityId, targetAccountId)
    items.value = items.value.filter((item) => item.securityId !== securityId)
  }

  /** 查询指定证券是否处于关注中。 */
  function findBySecurityId(securityId?: string | null): WatchlistItem | undefined {
    if (!securityId) return undefined
    return bySecurityId.value.get(securityId)
  }

  /** 退出登录或切账户时清空本地缓存。 */
  function clear(): void {
    requestGeneration += 1
    items.value = []
    accountId.value = ''
    error.value = ''
  }

  return {
    items,
    accountId,
    loading,
    error,
    bySecurityId,
    load,
    add,
    update,
    remove,
    findBySecurityId,
    clear,
  }
})
