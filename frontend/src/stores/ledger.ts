import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api/client'
import type { AnalyticsSummary, Position, Trade } from '../api/types'

export const useLedgerStore = defineStore('ledger', () => {
  const positions = ref<Position[]>([])
  const summary = ref<AnalyticsSummary | null>(null)
  const trades = ref<Trade[]>([])
  const loading = ref(false)
  const error = ref('')
  async function loadDashboard(includeClosed = false) {
    loading.value = true; error.value = ''
    try { [positions.value, summary.value] = await Promise.all([api.positions(includeClosed), api.summary()]) }
    catch (err) { error.value = err instanceof Error ? err.message : '加载账本失败' }
    finally { loading.value = false }
  }
  async function loadTrades() { trades.value = await api.trades() }
  return { positions, summary, trades, loading, error, loadDashboard, loadTrades }
})
