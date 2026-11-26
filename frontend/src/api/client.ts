import type { Account, AnalyticsSummary, ApiError, AuthMeRead, AuthUser, DashboardMode, DashboardRead, IntradayRead, InviteUserRead, LotsRead, Position, ProfitSetting, ProfitTarget, Quote, RecalculationRead, RequestCodeRead, Security, SecurityCandidate, SellPreview, Trade, TradeUpdate, TradeUpdateRead, WatchlistItem, WatchlistUpdatePayload, WatchlistUpsertPayload, WeComNotificationSetting, WeComNotificationTestResult, WeComNotificationUpdate } from './types'

const base = import.meta.env.VITE_API_BASE ?? '/api/v1'

let unauthorizedHandler: (() => void) | null = null

/** 注册全局 401 未登录回调，用于清理前端登录态并跳转登录页。 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

/** 统一发送 API 请求（默认携带同源 HttpOnly Cookie）并将领域错误转换为界面可读错误。 */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  })
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/')) {
      unauthorizedHandler?.()
    }
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
  /** 向受邀或已激活邮箱请求 6 位登录验证码。 */
  requestLoginCode: (email: string) => request<RequestCodeRead>('/auth/request-code', { method: 'POST', body: JSON.stringify({ email }) }),
  /** 提交邮箱与验证码完成登录并写入服务端会话 Cookie。 */
  verifyLoginCode: (email: string, code: string) => request<AuthMeRead>('/auth/verify-code', { method: 'POST', body: JSON.stringify({ email, code }) }),
  /** 读取当前已登录用户资料。 */
  me: () => request<AuthMeRead>('/auth/me'),
  /** 退出登录并吊销服务端会话。 */
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  /** 管理员查询系统内全部用户与待激活邀请。 */
  adminUsers: () => request<AuthUser[]>('/admin/users'),
  /** 管理员按邮箱定向邀请普通用户。 */
  adminInviteUser: (payload: { email: string; displayName?: string | null }) => request<InviteUserRead>('/admin/users/invite', { method: 'POST', body: JSON.stringify(payload) }),
  /** 管理员撤销尚未首登激活的邀请记录。 */
  adminRevokeInvite: (userId: string) => request<void>(`/admin/users/${userId}/invite`, { method: 'DELETE' }),
  /** 管理员切换普通用户启用或禁用状态。 */
  adminUpdateUserStatus: (userId: string, status: 'ACTIVE' | 'DISABLED') => request<AuthMeRead>(`/admin/users/${userId}/status`, { method: 'PATCH', body: JSON.stringify({ status }) }),
  accounts: () => request<Account[]>('/accounts'),
  createAccount: (payload: Record<string, unknown>) => request<Account>('/accounts', { method: 'POST', body: JSON.stringify(payload) }),
  positions: (includeClosed = false) => request<Position[]>(`/positions?${query({ includeClosed })}`),
  summary: (accountId?: string) => request<AnalyticsSummary>(`/analytics/summary?${query({ accountId })}`),
  /** 读取同时包含账务、行情和估值的一致性首页快照。 */
  dashboard: (accountId?: string, includeClosed = false, mode: DashboardMode = 'auto') => request<DashboardRead>(`/dashboard?${query({ accountId, includeClosed, mode })}`),
  quotes: (symbols?: string[], refresh = false) => request<{ quotes: Quote[] }>(`/quotes?${query({ symbols: symbols?.join(','), refresh })}`),
  securities: (search = '') => request<Security[]>(`/securities?${query({ query: search })}`),
  /** 通过外部行情源查找证券名称，查询本身不创建本地证券。 */
  lookupSecurities: (search: string) => request<{ securities: SecurityCandidate[]; error: string | null }>(`/securities/lookup?${query({ query: search })}`),
  /** 读取证券真实当日日内时序、场外基金估算分时或近期净值曲线及对应当前报价。 */
  intraday: (securityId: string, series: 'auto' | 'nav' = 'auto') => request<IntradayRead>(`/securities/${securityId}/intraday?${query({ series: series === 'auto' ? undefined : series })}`),
  /** 绑定、修改或清除场外基金的盘中估算跟踪参考标的。 */
  updateSecurityProxy: (securityId: string, payload: { proxySymbol: string | null; proxyName?: string | null; trackingRatio?: string | null }) => request<Security>(`/securities/${securityId}/proxy`, { method: 'PATCH', body: JSON.stringify(payload) }),
  createSecurity: (payload: Record<string, unknown>) => request<Security>('/securities', { method: 'POST', body: JSON.stringify(payload) }),
  /** 读取同一行情切片下的批次、浮盈和继承止盈目标。 */
  lots: (securityId: string, includeClosed = false, accountId?: string, strategyId?: string) => request<LotsRead>(`/securities/${securityId}/lots?${query({ includeClosed, accountId, strategyId })}`),
  /** 在指定账户、个股和策略范围内从原始交易重新构造派生批次。 */
  recalculate: (securityId: string, accountId?: string, strategyId?: string) => request<RecalculationRead>(`/securities/${securityId}/recalculate?${query({ accountId, strategyId })}`, { method: 'POST' }),
  /** 读取全局或个股明确保存的止盈设置。 */
  profitSetting: (securityId?: string) => request<ProfitSetting | null>(securityId ? `/securities/${securityId}/profit-settings` : '/profit-settings'),
  /** 保存本层止盈设置，空收益率表示清除本层覆盖。 */
  saveProfitSetting: (payload: { targetReturnPct: string | null; calculationBasis: 'NET' | 'GROSS'; alertEnabled: boolean }, securityId?: string) => request<ProfitSetting | null>(securityId ? `/securities/${securityId}/profit-settings` : '/profit-settings', { method: 'PUT', body: JSON.stringify(payload) }),
  trades: (accountId?: string) => request<Trade[]>(`/trades?${query({ accountId })}`),
  /** 按原版本更正成交字段，不直接替换派生持仓。 */
  updateTrade: (id: string, payload: TradeUpdate) => request<TradeUpdateRead>(`/trades/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  cycles: (securityId?: string, accountId?: string) => request<unknown[]>(`/cycles?${query({ securityId, accountId, includeClosed: true })}`),
  previewSell: (payload: Record<string, unknown>) => request<SellPreview>('/trades/sell/preview', { method: 'POST', body: JSON.stringify(payload) }),
  buy: (payload: Record<string, unknown>) => request<Trade>('/trades/buy', { method: 'POST', body: JSON.stringify(payload) }),
  sell: (payload: Record<string, unknown>) => request<Trade>('/trades/sell', { method: 'POST', body: JSON.stringify(payload) }),
  voidTrade: (id: string, reason: string) => request<Trade>(`/trades/${id}/void`, { method: 'POST', body: JSON.stringify({ reason }) }),
  createTarget: (lotId: string, payload: Record<string, unknown>) => request<ProfitTarget>(`/lots/${lotId}/targets`, { method: 'POST', body: JSON.stringify(payload) }),
  updateTarget: (id: string, payload: Record<string, unknown>) => request<ProfitTarget>(`/targets/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteTarget: (id: string) => request<void>(`/targets/${id}`, { method: 'DELETE' }),
  /** 读取当前用户的企业微信止盈推送脱敏配置。 */
  wecomNotification: () => request<WeComNotificationSetting>('/notifications/wecom'),
  /** 保存、更新或清空当前用户的企业微信止盈推送配置。 */
  saveWecomNotification: (payload: WeComNotificationUpdate) => request<WeComNotificationSetting>('/notifications/wecom', { method: 'PUT', body: JSON.stringify(payload) }),
  /** 发送一条企业微信测试消息以验证 Webhook 连通性。 */
  testWecomNotification: (webhookUrl?: string) => request<WeComNotificationTestResult>('/notifications/wecom/test', { method: 'POST', body: JSON.stringify(webhookUrl ? { webhookUrl } : {}) }),
  /** 读取当前账户的关注证券列表（独立于首页估值接口）。 */
  watchlist: (accountId?: string) => request<WatchlistItem[]>(`/watchlist?${query({ accountId })}`),
  /** 新增或幂等更新关注证券及目标买回价配置。 */
  addWatchlist: (payload: WatchlistUpsertPayload, accountId?: string) => request<WatchlistItem>(`/watchlist?${query({ accountId })}`, { method: 'POST', body: JSON.stringify(payload) }),
  /** 局部更新关注证券的目标买回价、企微提醒开关与备注。 */
  updateWatchlist: (securityId: string, payload: WatchlistUpdatePayload, accountId?: string) => request<WatchlistItem>(`/watchlist/${securityId}?${query({ accountId })}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  /** 取消关注指定证券。 */
  removeWatchlist: (securityId: string, accountId?: string) => request<void>(`/watchlist/${securityId}?${query({ accountId })}`, { method: 'DELETE' }),
}
