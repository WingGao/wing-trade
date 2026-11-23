export type Currency = 'CNY' | 'USD'

export type UserRole = 'ADMIN' | 'USER'

export type UserStatus = 'INVITED' | 'ACTIVE' | 'DISABLED'

export interface AuthUser {
  id: string
  email: string
  displayName?: string | null
  role: UserRole
  status: UserStatus
  isDefaultAdmin: boolean
  lastLoginAt?: string | null
  createdAt: string
}

export interface RequestCodeRead {
  sent: boolean
  expiresInSeconds: number
  cooldownSeconds: number
}

export interface AuthMeRead {
  user: AuthUser
}

export interface InviteUserRead {
  user: AuthUser
  inviteEmailSent: boolean
}

export type MatchingMethod = 'FIFO' | 'LIFO' | 'EQUAL' | 'MANUAL'

export type DashboardMode = 'cache' | 'auto' | 'manual'

export type ValuationStatus = 'fresh' | 'stale' | 'partial' | 'unavailable' | 'empty'

export interface Account {
  id: string
  name: string
  currency: Currency
  broker?: string | null
}

export interface Security {
  id: string
  market: string
  symbol: string
  name?: string | null
  securityType?: string
  status?: string
  currency?: Currency
}

export interface SecurityCandidate extends Omit<Security, 'id'> {
  id?: string
}

export interface ProfitSetting {
  targetReturnPct: string
  calculationBasis: 'NET' | 'GROSS'
  alertEnabled: boolean
}

export interface EffectiveTarget extends ProfitSetting {
  source: 'LOT' | 'SECURITY' | 'GLOBAL'
  id?: string
  plannedSellQty?: string
  targetPriceEstimate: string | null
}

export interface IntradayRead {
  symbol: string
  currency: Currency
  tradingDate: string
  seriesKind?: 'intraday' | 'nav'
  points: Array<{ time: string; price: string }>
  quote: Quote | null
  provider: string
  fetchedAt: string | null
  isStale: boolean
  error: string | null
}

export interface Quote {
  symbol: string
  securityId?: string | null
  lastPrice?: string | null
  prevClose?: string | null
  changePct?: string | null
  sourceTime?: string | null
  fetchedAt?: string | null
  provider?: string | null
  isStale: boolean
  market?: string | null
  currency?: Currency | null
}

export interface Position {
  securityId: string
  market?: string | null
  symbol?: string | null
  name?: string | null
  securityType?: string | null
  currency?: Currency | null
  strategyId: string
  remainingQty: string
  remainingCost: string
  averageCost: string
  realizedPnl: string
  unrealizedPnl?: string | null
  strategyBreakevenPrice?: string | null
  fundsRecoveredSurplus?: string | null
}

export interface Lot {
  id: string
  buyTradeId: string
  accountId: string
  securityId: string
  strategyId: string
  cycleId: string
  originalQty: string
  originalCost: string
  remainingQty: string
  remainingCost: string
  unitCost: string
  executedAt: string
  status: string
  targets?: ProfitTarget[]
  unrealizedPnl?: string | null
  unrealizedReturnPct?: string | null
  effectiveTarget?: EffectiveTarget | null
}

export interface LotsSummary {
  remainingQty: string
  remainingCost: string
  realizedPnl: string
  unrealizedPnl: string | null
}

export interface LotsRead {
  lots: Lot[]
  closedLotCount: number
  realizedPnl: string
  summary: LotsSummary
  quote?: Quote | null
  currency?: Currency
  closedTradeDetails?: SettledTradeDetail[]
  accountId?: string
  strategyId?: string
  needsRefresh?: boolean
}

export interface SettledTradeDetail {
  lotId: string
  tradeId: string
  side: 'BUY' | 'SELL'
  executedAt: string
  qty: string
  originalTradeQty: string
  price: string
  cashAmount: string
  realizedPnl: string | null
}

export interface ProfitTarget {
  id: string
  lotId: string
  targetReturnPct: string
  plannedSellQty: string
  calculationBasis: string
  targetPriceEstimate?: string | null
  alertEnabled: boolean
  status: string
  triggeredAt?: string | null
  createdAt?: string
}

export interface Trade {
  id: string
  accountId: string
  securityId: string
  strategyId: string
  side: 'BUY' | 'SELL'
  executedAt: string
  qty: string
  price: string
  grossAmount: string
  feesTotal: string
  commission: string
  stampDuty: string
  transferFee: string
  otherFee: string
  revision: number
  status: string
  clientRequestId?: string | null
  memo?: string | null
  currency?: Currency
  market?: string | null
  symbol?: string | null
  name?: string | null
  securityType?: string | null
}

export interface TradeUpdate {
  revision: number
  executedAt?: string
  qty?: string
  price?: string
  commission?: string
  stampDuty?: string
  transferFee?: string
  otherFee?: string
  memo?: string | null
  reason?: string
}

export interface TradeUpdateRead {
  trade: Trade
  needsRefresh: boolean
}

export interface PositionRefreshScope {
  accountId: string
  securityId: string
  strategyId?: string
  market?: string | null
  symbol?: string | null
  name?: string | null
  securityType?: string | null
}

export interface RecalculationRead {
  rebuiltLots: number
  rebuiltMatches: number
  needsRefresh?: boolean
}

export interface AnalyticsSummary {
  currency?: Currency
  totalBuyCashOut: string
  totalSellCashIn: string
  realizedPnl: string
  unrealizedPnl?: string | null
  remainingQty: string
  remainingCost: string
}

export interface LowestPriceGroup {
  purchasePrice: string
  lotCount: number
  remainingQty: string
  remainingCost: string
  unrealizedPnl: string | null
  unrealizedReturnPct: string | null
}

export interface DashboardPosition extends Position {
  quote: Quote | null
  unrealizedReturnPct: string | null
  lowestPriceGroup: LowestPriceGroup | null
  needsRefresh?: boolean
}

export interface DashboardSummary extends AnalyticsSummary {
  marketValue: string | null
  currency: Currency
  unrealizedPnl: string | null
  unrealizedReturnPct: string | null
  valuationStatus: ValuationStatus
  totalOpenPositions: number
  quotedOpenPositions: number
  staleOpenPositions: number
}

export interface DashboardRead {
  accountId: string
  currency: Currency
  asOf: string
  marketSessionOpen: boolean
  pollAfterSeconds: number
  positions: DashboardPosition[]
  summary: DashboardSummary
  quotes: Quote[]
  pendingRefreshScopes?: PositionRefreshScope[]
}

export interface SellPreviewMatch {
  lotId: string
  buyTradeId: string
  buyExecutedAt: string
  buyPrice: string
  buyQty: string
  availableQty: string
  matchedQty: string
  allocatedBuyCost: string
  allocatedSellNet: string
  realizedPnl: string
  matchingMethod: MatchingMethod
}

export interface SellPreview {
  currency: Currency
  executedAt: string
  qty: string
  price: string
  grossAmount: string
  feesTotal: string
  cashIn: string
  realizedPnl: string
  matches: SellPreviewMatch[]
}

export interface ApiError {
  error?: { code?: string; message?: string; details?: Record<string, unknown> }
}
