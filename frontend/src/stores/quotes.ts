import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api/client'
import type { Quote } from '../api/types'

export const useQuotesStore = defineStore('quotes', () => {
  const quotes = ref<Quote[]>([])
  const loading = ref(false)
  const lastUpdated = ref<Date | null>(null)
  async function refresh(symbols?: string[]) {
    loading.value = true
    try { quotes.value = (await api.quotes(symbols, true)).quotes; lastUpdated.value = new Date() }
    finally { loading.value = false }
  }
  async function load(symbols?: string[]) {
    try { quotes.value = (await api.quotes(symbols, false)).quotes; lastUpdated.value = new Date() } catch { /* ledger remains usable offline */ }
  }
  function bySecurity(securityId: string) { return quotes.value.find((quote) => quote.securityId === securityId) }
  return { quotes, loading, lastUpdated, refresh, load, bySecurity }
})
