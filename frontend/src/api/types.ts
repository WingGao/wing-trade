export interface Account {
  id: string
  name: string
  currency: 'CNY' | 'USD'
  broker?: string | null
}

export interface Security {
  id: string
  market: string
  symbol: string
  name?: string | null
  securityType?: string
  status?: string
  currency?: 'CNY' | 'USD'
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
  currency?: 'CNY' | 'USD' | null
}

export interface Position {
  securityId: string
  market?: string | null
  symbol?: string | null
  name?: string | null
  currency?: 'CNY' | 'USD' | null
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
  status: string
  clientRequestId?: string | null
  memo?: string | null
}

export interface AnalyticsSummary {
  currency?: 'CNY' | 'USD'
  totalBuyCashOut: string
  totalSellCashIn: string
  realizedPnl: string
  unrealizedPnl?: string | null
  remainingQty: string
  remainingCost: string
}

export interface SellPreview {
  qty: string
  price: string
  grossAmount: string
  feesTotal: string
  cashIn: string
  realizedPnl: string
  matches: Array<{ lotId: string; matchedQty: string; allocatedBuyCost: string; realizedPnl: string }>
}

export interface ApiError {
  error?: { code?: string; message?: string; details?: Record<string, unknown> }
}
