import type { Account, AnalyticsSummary, ApiError, DashboardMode, DashboardRead, IntradayRead, LotsRead, Position, ProfitSetting, ProfitTarget, Quote, Security, SecurityCandidate, SellPreview, Trade } from './types'

const base = import.meta.env.VITE_API_BASE ?? '/api/v1'

/** 统一发送 API 请求并将领域错误转换为界面可读错误。 */
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

/** 仅序列化明确提供的查询参数。 */
const query = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => value !== undefined && search.set(key, String(value)))
  return search.toString()
}

export const api = {
  accounts: () => request<Account[]>('/accounts'),
  createAccount: (payload: Record<string, unknown>) => request<Account>('/accounts', { method: 'POST', body: JSON.stringify(payload) }),
  positions: (includeClosed = false) => request<Position[]>(`/positions?${query({ includeClosed })}`),
  summary: () => request<AnalyticsSummary>(`/analytics/summary`),
  /** 读取同时包含账务、行情和估值的一致性首页快照。 */
  dashboard: (accountId?: string, includeClosed = false, mode: DashboardMode = 'auto') => request<DashboardRead>(`/dashboard?${query({ accountId, includeClosed, mode })}`),
  quotes: (symbols?: string[], refresh = false) => request<{ quotes: Quote[] }>(`/quotes?${query({ symbols: symbols?.join(','), refresh })}`),
  securities: (search = '') => request<Security[]>(`/securities?${query({ query: search })}`),
  /** 通过外部行情源查找证券名称，查询本身不创建本地证券。 */
  lookupSecurities: (search: string) => request<{ securities: SecurityCandidate[]; error: string | null }>(`/securities/lookup?${query({ query: search })}`),
  /** 读取证券真实当日日内时序及对应当前报价。 */
  intraday: (securityId: string) => request<IntradayRead>(`/securities/${securityId}/intraday`),
  createSecurity: (payload: Record<string, unknown>) => request<Security>('/securities', { method: 'POST', body: JSON.stringify(payload) }),
  /** 读取同一行情切片下的批次、浮盈和继承止盈目标。 */
  lots: (securityId: string, includeClosed = false, accountId?: string) => request<LotsRead>(`/securities/${securityId}/lots?${query({ includeClosed, accountId })}`),
  /** 在指定账户范围内从原始交易重新构造个股派生批次。 */
  recalculate: (securityId: string, accountId?: string) => request<{ rebuiltLots: number; rebuiltMatches: number }>(`/securities/${securityId}/recalculate?${query({ accountId })}`, { method: 'POST' }),
  /** 读取全局或个股明确保存的止盈设置。 */
  profitSetting: (securityId?: string) => request<ProfitSetting | null>(securityId ? `/securities/${securityId}/profit-settings` : '/profit-settings'),
  /** 保存本层止盈设置，空收益率表示清除本层覆盖。 */
  saveProfitSetting: (payload: { targetReturnPct: string | null; calculationBasis: 'NET' | 'GROSS'; alertEnabled: boolean }, securityId?: string) => request<ProfitSetting | null>(securityId ? `/securities/${securityId}/profit-settings` : '/profit-settings', { method: 'PUT', body: JSON.stringify(payload) }),
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
