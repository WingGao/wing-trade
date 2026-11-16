import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '../api/client'
import type { DashboardMode, DashboardRead, Trade } from '../api/types'

export interface DashboardLoadOptions {
  accountId?: string
  includeClosed?: boolean
  mode?: DashboardMode
  reuse?: boolean
}

interface ActiveAutoRequest {
  key: string
  sequence: number
  promise: Promise<DashboardRead | null>
}

export const useLedgerStore = defineStore('ledger', () => {
  const dashboard = ref<DashboardRead | null>(null)
  const trades = ref<Trade[]>([])
  const loading = ref(false)
  const error = ref('')
  const positions = computed(() => dashboard.value?.positions ?? [])
  const summary = computed(() => dashboard.value?.summary ?? null)

  let queueTail: Promise<void> = Promise.resolve()
  let requestSequence = 0
  let pendingRequests = 0
  let activeAutoRequest: ActiveAutoRequest | null = null

  /** 将新旧调用参数统一成完整的首页加载选项。 */
  function normalizeOptions(options: DashboardLoadOptions | boolean): Required<Omit<DashboardLoadOptions, 'accountId'>> & Pick<DashboardLoadOptions, 'accountId'> {
    if (typeof options === 'boolean') {
      return { accountId: undefined, includeClosed: options, mode: 'auto', reuse: true }
    }
    return {
      accountId: options.accountId,
      includeClosed: options.includeClosed ?? false,
      mode: options.mode ?? 'auto',
      reuse: options.reuse ?? true,
    }
  }

  /**
   * 读取一份完整首页估值快照，并通过串行队列避免刷新请求重叠。
   * 同一查询的自动刷新会复用在途请求，写入时只接受最新代次的结果。
   */
  async function loadDashboard(options: DashboardLoadOptions | boolean = {}): Promise<DashboardRead | null> {
    const normalized = normalizeOptions(options)
    const key = `${normalized.accountId ?? ''}|${normalized.includeClosed}|${normalized.mode}`

    if (normalized.mode === 'auto' && normalized.reuse && activeAutoRequest?.key === key && activeAutoRequest.sequence === requestSequence) {
      return activeAutoRequest.promise
    }

    const requestId = ++requestSequence
    pendingRequests += 1
    loading.value = true
    error.value = ''

    /** 在当前请求代次仍为最新时，将整份响应替换到首页状态。 */
    const execute = async (): Promise<DashboardRead | null> => {
      try {
        const result = await api.dashboard(normalized.accountId, normalized.includeClosed, normalized.mode)
        if (requestId === requestSequence) dashboard.value = result
        return result
      } catch (cause) {
        if (requestId === requestSequence) {
          error.value = cause instanceof Error ? cause.message : '加载首页估值失败'
        }
        return null
      } finally {
        pendingRequests = Math.max(0, pendingRequests - 1)
        loading.value = pendingRequests > 0
      }
    }

    const promise: Promise<DashboardRead | null> = queueTail.then(execute, execute)
    queueTail = promise.then(() => undefined, () => undefined)
    if (normalized.mode === 'auto') activeAutoRequest = { key, sequence: requestId, promise }
    void promise.finally(() => {
      if (activeAutoRequest?.promise === promise) activeAutoRequest = null
    })
    return promise
  }

  /** 读取交易流水，保留交易页原有数据接口。 */
  async function loadTrades() {
    trades.value = await api.trades()
  }

  return { dashboard, positions, summary, trades, loading, error, loadDashboard, loadTrades }
})
