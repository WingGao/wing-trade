import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '../api/client'
import type { Quote, QuoteStreamState } from '../api/types'

const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 30000]
const OFFLINE_FAILURE_THRESHOLD = 3

interface StreamSnapshotPayload {
  serverTime?: string
  provider?: string
  streamState?: string
  quotes?: Quote[]
}

interface StreamQuotePayload {
  serverTime?: string
  quotes?: Quote[]
}

interface StreamStatusPayload {
  provider?: string
  streamState?: string
  message?: string
}

/**
 * 全局实时行情 Store：维护应用级单一 SSE EventSource 长连接、断线退避重连与临时标的订阅。
 */
export const useQuotesStore = defineStore('quotes', () => {
  const quotesBySymbol = ref<Record<string, Quote>>({})
  const connectionState = ref<QuoteStreamState>('idle')
  const upstreamState = ref<string>('idle')
  const provider = ref<string>('')
  const statusMessage = ref<string>('')
  const loading = ref(false)
  const lastUpdated = ref<Date | null>(null)

  const quotes = computed<Quote[]>(() => Object.values(quotesBySymbol.value))
  const quotesMap = computed<ReadonlyMap<string, Quote>>(
    () => new Map(Object.entries(quotesBySymbol.value)),
  )
  const isLive = computed(() => connectionState.value === 'live')

  const extraSymbolRefs = new Map<string, number>()
  let eventSource: EventSource | null = null
  let reconnectTimer: number | null = null
  let reconnectAttempts = 0
  let shouldStayConnected = false
  let connectedSymbolsKey = ''
  let visibilityBound = false

  /** 将一批新报价按规范大写代码合并入内存映射表。 */
  function ingestQuotes(items: Quote[] | undefined): void {
    if (!items || !items.length) return
    const next = { ...quotesBySymbol.value }
    let changed = false
    for (const item of items) {
      const key = item.symbol.trim().toUpperCase()
      if (!key) continue
      next[key] = { ...item, symbol: key }
      changed = true
    }
    if (changed) {
      quotesBySymbol.value = next
      lastUpdated.value = new Date()
    }
  }

  /** 构建当前附带临时关注标的的 SSE 订阅地址。 */
  function buildStreamUrl(): { url: string; symbolsKey: string } {
    const extra = [...extraSymbolRefs.keys()].sort()
    const symbolsKey = extra.join(',')
    const query = symbolsKey ? `?symbols=${encodeURIComponent(symbolsKey)}` : ''
    return { url: `/api/v1/quotes/stream${query}`, symbolsKey }
  }

  /** 清理待执行的重连定时器。 */
  function clearReconnectTimer(): void {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  /** 关闭当前底层 EventSource 实例。 */
  function closeSource(): void {
    if (eventSource !== null) {
      eventSource.close()
      eventSource = null
    }
  }

  /** 按指数退避安排下一次 SSE 重连，连续失败达阈值时降级为 offline 供页面回退轮询。 */
  function scheduleReconnect(): void {
    clearReconnectTimer()
    if (!shouldStayConnected) return
    reconnectAttempts += 1
    connectionState.value = reconnectAttempts >= OFFLINE_FAILURE_THRESHOLD ? 'offline' : 'reconnecting'
    const delay = RECONNECT_DELAYS_MS[Math.min(reconnectAttempts - 1, RECONNECT_DELAYS_MS.length - 1)] ?? 30000
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      if (shouldStayConnected) openSource()
    }, delay)
  }

  /** 建立底层 EventSource 并绑定 snapshot / quote / status 监听。 */
  function openSource(): void {
    if (!shouldStayConnected) return
    if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
      connectionState.value = 'offline'
      return
    }
    closeSource()
    clearReconnectTimer()
    const { url, symbolsKey } = buildStreamUrl()
    connectedSymbolsKey = symbolsKey
    connectionState.value = reconnectAttempts > 0 ? 'reconnecting' : 'connecting'

    let source: EventSource
    try {
      source = new window.EventSource(url, { withCredentials: true })
    } catch {
      scheduleReconnect()
      return
    }
    eventSource = source

    source.addEventListener('snapshot', (rawEvent) => {
      if (source !== eventSource) return
      reconnectAttempts = 0
      connectionState.value = 'live'
      try {
        const payload = JSON.parse((rawEvent as MessageEvent<string>).data) as StreamSnapshotPayload
        if (payload.provider) provider.value = payload.provider
        if (payload.streamState) upstreamState.value = payload.streamState
        ingestQuotes(payload.quotes)
      } catch {
        // 忽略单帧解析异常，保持流存活
      }
    })

    source.addEventListener('quote', (rawEvent) => {
      if (source !== eventSource) return
      reconnectAttempts = 0
      connectionState.value = 'live'
      try {
        const payload = JSON.parse((rawEvent as MessageEvent<string>).data) as StreamQuotePayload
        ingestQuotes(payload.quotes)
      } catch {
        // 忽略单帧解析异常
      }
    })

    source.addEventListener('status', (rawEvent) => {
      if (source !== eventSource) return
      try {
        const payload = JSON.parse((rawEvent as MessageEvent<string>).data) as StreamStatusPayload
        if (payload.provider) provider.value = payload.provider
        if (payload.streamState) upstreamState.value = payload.streamState
        if (payload.message !== undefined) statusMessage.value = payload.message
      } catch {
        // 忽略状态帧解析异常
      }
    })

    source.onerror = () => {
      if (source !== eventSource) return
      closeSource()
      scheduleReconnect()
    }
  }

  /** 页面从后台恢复可见时，若连接处于重连或离线态则立即尝试恢复 SSE。 */
  function handleVisibilityChange(): void {
    if (!shouldStayConnected || document.hidden) return
    if (connectionState.value === 'offline' || connectionState.value === 'reconnecting') {
      reconnectAttempts = 0
      openSource()
    }
  }

  /** 启动并保活全局 SSE 行情推送连接。 */
  function connect(): void {
    shouldStayConnected = true
    if (typeof document !== 'undefined' && !visibilityBound) {
      document.addEventListener('visibilitychange', handleVisibilityChange)
      visibilityBound = true
    }
    const { symbolsKey } = buildStreamUrl()
    if (eventSource !== null && symbolsKey === connectedSymbolsKey) return
    openSource()
  }

  /** 断开全局 SSE 连接，但保留已有内存报价供离线视图展示。 */
  function disconnect(): void {
    shouldStayConnected = false
    clearReconnectTimer()
    closeSource()
    if (typeof document !== 'undefined' && visibilityBound) {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      visibilityBound = false
    }
    connectionState.value = 'idle'
    reconnectAttempts = 0
    connectedSymbolsKey = ''
  }

  /** 登出或会话失效时彻底断开连接并清空内存报价与临时订阅。 */
  function reset(): void {
    disconnect()
    quotesBySymbol.value = {}
    extraSymbolRefs.clear()
    upstreamState.value = 'idle'
    provider.value = ''
    statusMessage.value = ''
    lastUpdated.value = null
  }

  /** 为详情页等场景登记临时关注标的，返回离开时调用的清理函数。 */
  function watchSymbol(rawSymbol: string): () => void {
    const key = rawSymbol.trim().toUpperCase()
    if (!key) return () => {}
    const prev = extraSymbolRefs.get(key) ?? 0
    extraSymbolRefs.set(key, prev + 1)
    if (prev === 0 && shouldStayConnected && !(key in quotesBySymbol.value)) {
      // 仅当该标的不在当前已推送集合中时平滑重连以扩展服务端临时订阅
      openSource()
    }
    let released = false
    return () => {
      if (released) return
      released = true
      const remaining = (extraSymbolRefs.get(key) ?? 0) - 1
      if (remaining <= 0) extraSymbolRefs.delete(key)
      else extraSymbolRefs.set(key, remaining)
    }
  }

  /** 按规范代码读取内存中的最新实时报价。 */
  function bySymbol(symbol: string | null | undefined): Quote | undefined {
    if (!symbol) return undefined
    return quotesBySymbol.value[symbol.trim().toUpperCase()]
  }

  /** 按证券 ID 读取内存中的最新实时报价（兼容旧接口）。 */
  function bySecurity(securityId: string): Quote | undefined {
    return quotes.value.find((quote) => quote.securityId === securityId)
  }

  /** 手动触发 REST 行情刷新并同步写入内存映射表。 */
  async function refresh(symbols?: string[]): Promise<void> {
    loading.value = true
    try {
      ingestQuotes((await api.quotes(symbols, true)).quotes)
    } finally {
      loading.value = false
    }
  }

  /** 静默读取 REST 缓存行情并合并进内存映射表。 */
  async function load(symbols?: string[]): Promise<void> {
    try {
      ingestQuotes((await api.quotes(symbols, false)).quotes)
    } catch {
      /* ledger remains usable offline */
    }
  }

  return {
    quotes,
    quotesBySymbol,
    quotesMap,
    connectionState,
    upstreamState,
    provider,
    statusMessage,
    isLive,
    loading,
    lastUpdated,
    connect,
    disconnect,
    reset,
    watchSymbol,
    bySymbol,
    bySecurity,
    refresh,
    load,
  }
})
