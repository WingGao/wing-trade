import type { AnalyticsSummary, ApiError, Lot, Position, ProfitTarget, Quote, Security, SellPreview, Trade } from './types'

const base = import.meta.env.VITE_API_BASE ?? '/api/v1'

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ApiError
    throw new Error(payload.error?.message ?? `请求失败（${response.status}）`)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const query = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => value !== undefined && search.set(key, String(value)))
  return search.toString()
}

export const api = {
  positions: (includeClosed = false) => request<Position[]>(`/positions?${query({ includeClosed })}`),
  summary: () => request<AnalyticsSummary>(`/analytics/summary`),
  quotes: (symbols?: string[], refresh = false) => request<{ quotes: Quote[] }>(`/quotes?${query({ symbols: symbols?.join(','), refresh })}`),
  securities: (search = '') => request<Security[]>(`/securities?${query({ query: search })}`),
  lots: (securityId: string, includeClosed = false) => request<{ lots: Lot[]; closedLotCount: number; realizedPnl: string }>(`/securities/${securityId}/lots?${query({ includeClosed })}`),
  trades: () => request<Trade[]>('/trades'),
  cycles: (securityId?: string) => request<unknown[]>(`/cycles?${query({ securityId, includeClosed: true })}`),
  previewSell: (payload: Record<string, unknown>) => request<SellPreview>('/trades/sell/preview', { method: 'POST', body: JSON.stringify(payload) }),
  buy: (payload: Record<string, unknown>) => request<Trade>('/trades/buy', { method: 'POST', body: JSON.stringify(payload) }),
  sell: (payload: Record<string, unknown>) => request<Trade>('/trades/sell', { method: 'POST', body: JSON.stringify(payload) }),
  voidTrade: (id: string, reason: string) => request<Trade>(`/trades/${id}/void`, { method: 'POST', body: JSON.stringify({ reason }) }),
  createTarget: (lotId: string, payload: Record<string, unknown>) => request<ProfitTarget>(`/lots/${lotId}/targets`, { method: 'POST', body: JSON.stringify(payload) }),
  updateTarget: (id: string, payload: Record<string, unknown>) => request<ProfitTarget>(`/targets/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteTarget: (id: string) => request<void>(`/targets/${id}`, { method: 'DELETE' }),
}
