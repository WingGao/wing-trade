<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type {
  Account,
  Currency,
  IntradayRead,
  Lot,
  LotsRead,
  LotsSummary,
  PositionRefreshScope,
  Quote,
  Security,
  SettledTradeDetail,
} from '../api/types'
import LotTable from './LotTable.vue'
import SettledTradeTable from './SettledTradeTable.vue'
import BuyTradeDialog from './BuyTradeDialog.vue'
import SellMatchDrawer from './SellMatchDrawer.vue'
import ProfitTargetDialog from './ProfitTargetDialog.vue'
import ProfitSettingDialog from './ProfitSettingDialog.vue'
import FundProxyDialog from './FundProxyDialog.vue'
import WatchlistSettingDialog from './WatchlistSettingDialog.vue'
import IntradayChart from './IntradayChart.vue'
import QuoteBadge from './QuoteBadge.vue'
import { useAccountsStore } from '../stores/accounts'
import { usePreferencesStore } from '../stores/preferences'
import { useLedgerStore } from '../stores/ledger'
import { useQuotesStore } from '../stores/quotes'
import { useWatchlistStore } from '../stores/watchlist'
import {
  formatDateTime,
  formatMoney,
  formatQuantity,
  formatSignedPercent,
  isFundSecurity,
  priceDigits,
  trendClass,
} from '../utils/format'
import { isPositionRefreshing, refreshPosition } from '../utils/recalculate'
import {
  canonicalSymbolKey,
  mergeQuoteIntoIntraday,
  pickNewerQuote,
  projectLotsWithQuote,
} from '../utils/valuation'
import { useViewport } from '../utils/viewport'

const props = withDefaults(defineProps<{
  market: string
  symbol: string
  accountId?: string
  strategyId?: string
  embedded?: boolean
}>(), {
  accountId: undefined,
  strategyId: undefined,
  embedded: false,
})

const emit = defineEmits<{
  (e: 'mutated'): void
  (e: 'account-change', accountId: string): void
  (e: 'open-standalone'): void
}>()

const accountsStore = useAccountsStore()
const preferences = usePreferencesStore()
const ledger = useLedgerStore()
const quotesStore = useQuotesStore()
const watchlistStore = useWatchlistStore()
const { isMobile } = useViewport()

const security = ref<Security | null>(null)
const accounts = ref<Account[]>([])
const accountId = ref('')
const lots = ref<Lot[]>([])
const holdingSummary = ref<LotsSummary | null>(null)
const closedTradeDetails = ref<SettledTradeDetail[]>([])
const closedLotCount = ref(0)
const lotQuote = ref<Quote | null>(null)
const intraday = ref<IntradayRead | null>(null)
const loading = ref(false)
const needsRefresh = ref(false)
const resolvedStrategyId = ref<string>()
const activeMutations = ref(0)
const mutationActive = computed(() => activeMutations.value > 0)
const pageError = ref('')
const marketError = ref('')
const buyVisible = ref(false)
const sellVisible = ref(false)
const sellInitialQty = ref('')
const targetVisible = ref(false)
const globalTargetVisible = ref(false)
const securityTargetVisible = ref(false)
const proxyDialogVisible = ref(false)
const watchlistDialogVisible = ref(false)
const togglingWatch = ref(false)
const fundSeriesMode = ref<'auto' | 'nav'>('auto')
const mobileActionsOpen = ref(false)
const mobileChartOpen = ref(false)
const selectedLotId = ref('')

const selectedLot = computed(() => lots.value.find((lot) => lot.id === selectedLotId.value))
const normalizedMarket = computed(() => props.market.trim().toUpperCase())
const normalizedSymbol = computed(() => props.symbol.trim().toUpperCase())
const isFund = computed(() => isFundSecurity(security.value ?? { market: normalizedMarket.value }))
const hasProxyBinding = computed(() => Boolean(isFund.value && security.value?.proxySymbol))
const proxySummaryText = computed(() => {
  if (!hasProxyBinding.value || !security.value?.proxySymbol) return ''
  const label = security.value.proxyName || security.value.proxySymbol
  const ratio = security.value.trackingRatio ? Number(security.value.trackingRatio) * 100 : 95
  const ratioText = Number.isFinite(ratio)
    ? `${Number.isInteger(ratio) ? ratio : ratio.toFixed(1).replace(/\.0$/, '')}%`
    : '95%'
  return `跟踪：${label} (${security.value.proxySymbol}) · ${ratioText}`
})
const currency = computed<Currency>(() =>
  security.value?.currency ?? ((security.value?.market ?? normalizedMarket.value).toUpperCase() === 'US' ? 'USD' : 'CNY'),
)
const matchingAccounts = computed(() => accounts.value.filter((account) => account.currency === currency.value))
const requestedStrategyId = computed(() => props.strategyId || undefined)
const refreshScope = computed<PositionRefreshScope | null>(() =>
  security.value && accountId.value
    ? {
        accountId: accountId.value,
        securityId: security.value.id,
        strategyId: resolvedStrategyId.value ?? requestedStrategyId.value,
        market: security.value.market,
        symbol: security.value.symbol,
        name: security.value.name,
      }
    : null,
)
const rebuilding = computed(() => (refreshScope.value ? isPositionRefreshing(refreshScope.value) : false))
const canonicalKey = computed(() =>
  canonicalSymbolKey(
    security.value?.market ?? normalizedMarket.value,
    security.value?.symbol ?? normalizedSymbol.value,
  ),
)
const liveQuote = computed(() => quotesStore.bySymbol(canonicalKey.value))
const displayIntraday = computed<IntradayRead | null>(() =>
  intraday.value ? mergeQuoteIntoIntraday(intraday.value, liveQuote.value ?? null) : null,
)
const currentQuote = computed(() => {
  const baseQuote = lotQuote.value ?? intraday.value?.quote ?? null
  const merged = pickNewerQuote(baseQuote, liveQuote.value)
  if (!merged) return null
  return marketError.value || displayIntraday.value?.isStale ? { ...merged, isStale: true } : merged
})
const projectedLotsState = computed(() =>
  projectLotsWithQuote(lots.value, holdingSummary.value, currentQuote.value),
)
const displayLots = computed(() => projectedLotsState.value.lots)
const displayHoldingSummary = computed(() => projectedLotsState.value.summary)
/** 判断有价汇总是否只能作为过期参考展示。 */
const holdingSummaryStale = computed(
  () =>
    displayHoldingSummary.value?.unrealizedPnl !== null
    && displayHoldingSummary.value?.unrealizedPnl !== undefined
    && Boolean(currentQuote.value?.isStale),
)
const hasOpenLots = computed(() => displayLots.value.some((lot) => lot.status === 'OPEN'))
/** 判断服务端汇总是否包含仍有剩余数量的未结清批次。 */
const hasRemainingHolding = computed(() =>
  Boolean(displayHoldingSummary.value && /[1-9]/.test(displayHoldingSummary.value.remainingQty)),
)
const lastSellPriceText = computed(() => {
  const price = displayHoldingSummary.value?.lastSellPrice
  if (!price) return '暂无卖出'
  return formatMoney(price, currency.value, priceDigits(isFund.value, 3))
})
const lastSellTimeTitle = computed(() => {
  const executedAt = displayHoldingSummary.value?.lastSellExecutedAt
  return executedAt ? `最近卖出时间：${formatDateTime(executedAt)}` : ''
})

let alive = false
let generation = 0
let lotGeneration = 0
let dataGeneration = 0
let pollTimer: number | null = null
let marketFlight: { generation: number; promise: Promise<void> } | null = null
let stopWatchSymbol: (() => void) | null = null

/** 切换移动端详情页快捷操作栏的展开或收起状态。 */
function toggleMobileActions(): void {
  mobileActionsOpen.value = !mobileActionsOpen.value
}

/** 切换移动端详情页日内分时图区域的展开或收起状态。 */
function toggleMobileChart(): void {
  mobileChartOpen.value = !mobileChartOpen.value
}

/** 释放当前详情面板在全局行情 Store 中登记的临时订阅。 */
function releaseWatchSymbol(): void {
  if (stopWatchSymbol) {
    stopWatchSymbol()
    stopWatchSymbol = null
  }
}

/** 清理轮询，防止离页、隐藏或重新加载后遗留计时器。 */
function clearTimer(): void {
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = null
}

/** 统一替换后端返回的批次估值，不在浏览器重算账务。 */
function applyLots(result: LotsRead): void {
  lots.value = result.lots
  holdingSummary.value = result.summary
  closedTradeDetails.value = preferences.includeClosed ? result.closedTradeDetails ?? [] : []
  closedLotCount.value = result.closedLotCount
  lotQuote.value = result.quote ?? null
  needsRefresh.value = result.needsRefresh ?? false
  resolvedStrategyId.value = result.strategyId ?? requestedStrategyId.value ?? result.lots[0]?.strategyId
}

/** 加载当前账户批次，查询条件改变后忽略旧响应。 */
async function loadLots(afterWrite = false): Promise<boolean> {
  if (mutationActive.value && !afterWrite) return false
  const currentSecurity = security.value
  const account = accountId.value
  const strategy = requestedStrategyId.value
  const request = ++lotGeneration
  const currentGeneration = generation
  const currentDataGeneration = dataGeneration
  const includeClosed = preferences.includeClosed
  if (!currentSecurity || !account) {
    lots.value = []
    holdingSummary.value = null
    closedTradeDetails.value = []
    needsRefresh.value = false
    return false
  }
  let result: LotsRead
  try {
    result = await api.lots(currentSecurity.id, includeClosed, account, strategy)
  } catch (cause) {
    if (
      alive
      && currentGeneration === generation
      && currentDataGeneration === dataGeneration
      && request === lotGeneration
      && account === accountId.value
      && strategy === requestedStrategyId.value
      && includeClosed === preferences.includeClosed
    ) {
      holdingSummary.value = null
    }
    throw cause
  }
  if (
    !alive
    || currentGeneration !== generation
    || currentDataGeneration !== dataGeneration
    || request !== lotGeneration
    || account !== accountId.value
    || strategy !== requestedStrategyId.value
    || includeClosed !== preferences.includeClosed
  ) {
    return false
  }
  if ((result.accountId && result.accountId !== account) || (result.strategyId && strategy && result.strategyId !== strategy)) {
    holdingSummary.value = null
    throw new Error('返回的持仓范围与当前账户、策略不一致')
  }
  applyLots(result)
  return true
}

/** 每次行情请求完成三秒后再刷新；SSE 实时推送活跃或未绑定跟踪标的的场外基金均不开启 3 秒轮询。 */
function schedulePoll(): void {
  clearTimer()
  if (!alive || document.hidden || !security.value || mutationActive.value || quotesStore.isLive) return
  if (isFund.value && (!hasProxyBinding.value || fundSeriesMode.value === 'nav')) return
  pollTimer = window.setTimeout(() => {
    pollTimer = null
    void refreshMarket()
  }, 3000)
}

/** 先获取真实日内行情，再读取同份缓存计算的批次浮盈，失败保留上一份数据。 */
async function refreshMarket(): Promise<void> {
  if (!alive || document.hidden || !security.value || mutationActive.value) return
  if (marketFlight?.generation === generation) return marketFlight.promise
  const currentGeneration = generation
  const currentDataGeneration = dataGeneration
  const id = security.value.id
  const series = hasProxyBinding.value ? fundSeriesMode.value : 'auto'
  /** 为当前证券代次执行一次串行行情与批次刷新。 */
  const execute = async () => {
    try {
      const result = await api.intraday(id, series)
      if (!alive || currentGeneration !== generation || currentDataGeneration !== dataGeneration || mutationActive.value) return
      await loadLots()
      if (!alive || currentGeneration !== generation || currentDataGeneration !== dataGeneration) return
      intraday.value = result
      marketError.value = result.error ?? ''
    } catch (cause) {
      if (alive && currentGeneration === generation && currentDataGeneration === dataGeneration) {
        marketError.value = cause instanceof Error ? cause.message : '行情刷新失败'
      }
    } finally {
      if (marketFlight?.generation === currentGeneration && currentDataGeneration === dataGeneration) marketFlight = null
      if (alive && currentGeneration === generation && currentDataGeneration === dataGeneration) schedulePoll()
    }
  }
  const promise = execute()
  marketFlight = { generation: currentGeneration, promise }
  return promise
}

/** 证券或账户范围切换时重新定位证券和同币种账户，并使旧证券的所有响应失效。 */
async function loadPanel(): Promise<void> {
  const request = ++generation
  lotGeneration += 1
  dataGeneration += 1
  marketFlight = null
  clearTimer()
  loading.value = true
  pageError.value = ''
  marketError.value = ''
  security.value = null
  lots.value = []
  holdingSummary.value = null
  closedTradeDetails.value = []
  lotQuote.value = null
  intraday.value = null
  needsRefresh.value = false
  resolvedStrategyId.value = undefined
  buyVisible.value = false
  sellVisible.value = false
  sellInitialQty.value = ''
  targetVisible.value = false
  securityTargetVisible.value = false
  proxyDialogVisible.value = false
  fundSeriesMode.value = 'auto'
  try {
    const market = normalizedMarket.value
    const symbol = normalizedSymbol.value
    if (!market || !symbol) {
      loading.value = false
      return
    }
    const [found, availableAccounts] = await Promise.all([api.securities(symbol), accountsStore.loadAccounts()])
    if (!alive || request !== generation) return
    security.value = found.find((item) => item.market.toUpperCase() === market && item.symbol.toUpperCase() === symbol) ?? null
    accounts.value = availableAccounts
    if (!security.value) throw new Error('找不到该证券，请先在总览中查找并添加')
    releaseWatchSymbol()
    stopWatchSymbol = quotesStore.watchSymbol(`${security.value.market}:${security.value.symbol}`)
    const requestedAccount = props.accountId || undefined
    if (requestedAccount && !matchingAccounts.value.some((account) => account.id === requestedAccount)) {
      throw new Error('指定账户不存在或与当前证券币种不匹配')
    }
    if (requestedAccount) accountId.value = requestedAccount
    else if (!matchingAccounts.value.some((account) => account.id === accountId.value)) {
      accountId.value = matchingAccounts.value[0]?.id ?? ''
    }
    if (!props.embedded && accountId.value) {
      accountsStore.setActiveAccount(accountId.value)
      emit('account-change', accountId.value)
    }
    await loadLots()
    if (alive && request === generation) void refreshMarket()
  } catch (cause) {
    if (request === generation) pageError.value = cause instanceof Error ? cause.message : '加载个股失败'
  } finally {
    if (request === generation) loading.value = false
  }
}

/** 操作成功或显示条件改变后，更新批次并通知父页面同步刷新。 */
async function reloadLots(notifyMutated = false): Promise<void> {
  invalidateReads()
  try {
    await loadLots(true)
    if (notifyMutated) emit('mutated')
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '刷新批次失败')
  } finally {
    if (!mutationActive.value) schedulePoll()
  }
}

/** 写入前后废弃批次和行情旧响应，并同步失效首页快照读取。 */
function invalidateReads(): void {
  dataGeneration += 1
  lotGeneration += 1
  marketFlight = null
  clearTimer()
  ledger.invalidateDashboard()
}

/** 重算和待刷新入口共享确认、防重及成功后的数据读取流程。 */
async function recalculate(): Promise<void> {
  const scope = refreshScope.value
  if (!scope) return
  const request = generation
  let started = false
  await refreshPosition(scope, {
    isCurrent: () =>
      alive
      && request === generation
      && scope.accountId === accountId.value
      && scope.securityId === security.value?.id
      && scope.strategyId === refreshScope.value?.strategyId,
    invalidate: () => {
      if (!started) {
        started = true
        activeMutations.value += 1
      }
      invalidateReads()
    },
    reload: async () => {
      const ok = await loadLots(true)
      if (ok) emit('mutated')
      return ok
    },
    settled: () => {
      activeMutations.value -= 1
      if (!alive) return
      if (request !== generation && !mutationActive.value && !loading.value) void reloadLots()
      else schedulePoll()
    },
  })
}

/** 将设置操作锚定至本行批次，不在列表外生成批次按钮。 */
function addTarget(lot: Lot): void {
  selectedLotId.value = lot.id
  targetVisible.value = true
}

/** 打开卖出配对抽屉，并按需预填指定数量。 */
function openSellDrawer(initialQty = ''): void {
  sellInitialQty.value = initialQty
  sellVisible.value = true
}

/** 点击持仓批次的卖出按钮，将该批次或合并组的剩余数量带入卖出弹窗。 */
function sellLot(remainingQty: string): void {
  openSellDrawer(remainingQty)
}

/** 跟踪参考标的保存或解绑后，立即刷新详情页行情、估算分时与批次浮盈。 */
function handleProxySaved(updated: Security): void {
  security.value = updated
  fundSeriesMode.value = 'auto'
  invalidateReads()
  emit('mutated')
  void refreshMarket()
}

/** 在场外基金盘中估算分时与近 30 日官方净值曲线之间切换展示。 */
function switchFundSeries(mode: 'auto' | 'nav'): void {
  if (fundSeriesMode.value === mode) return
  fundSeriesMode.value = mode
  marketFlight = null
  clearTimer()
  void refreshMarket()
}

/** 标签页恢复可见立即刷新，隐藏时不再安排请求。 */
function handleVisibility(): void {
  clearTimer()
  if (!document.hidden) void refreshMarket()
}

watch(() => quotesStore.connectionState, (state) => {
  if (!alive) return
  if (state === 'live') clearTimer()
  else if (state === 'offline') schedulePoll()
})

const currentWatchlistItem = computed(() => watchlistStore.findBySecurityId(security.value?.id) ?? null)
const watchButtonLabel = computed(() => {
  const item = currentWatchlistItem.value
  if (!item) return '☆ 关注'
  if (item.targetBuyPrice) {
    return `★ 买回 ${formatMoney(item.targetBuyPrice, currency.value, priceDigits(isFund.value, 2))}`
  }
  return '★ 已关注'
})

/** 未关注时一键加入关注，已关注时打开买回价与提醒配置弹窗。 */
async function handleWatchAction(): Promise<void> {
  if (!security.value || !accountId.value || togglingWatch.value) return
  if (currentWatchlistItem.value) {
    watchlistDialogVisible.value = true
    return
  }
  togglingWatch.value = true
  try {
    await watchlistStore.add(
      { securityId: security.value.id, accountId: accountId.value },
      accountId.value,
    )
    ElMessage.success('已加入关注，可再次点击设置目标买回价')
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '加入关注失败')
  } finally {
    togglingWatch.value = false
  }
}

watch(
  accountId,
  (acc) => {
    if (acc && watchlistStore.accountId !== acc) {
      void watchlistStore.load(acc)
    }
  },
  { immediate: true },
)

watch(
  () => [props.market, props.symbol, props.accountId, props.strategyId],
  () => {
    if (alive) void loadPanel()
  },
)

watch(
  () => [accountId.value, preferences.includeClosed] as const,
  ([account], previous) => {
    closedTradeDetails.value = []
    if (alive && !loading.value) {
      const accountChanged = account !== previous[0]
      if (accountChanged) {
        generation += 1
        resolvedStrategyId.value = undefined
        lots.value = []
        holdingSummary.value = null
        needsRefresh.value = false
        if (account && watchlistStore.accountId !== account) {
          void watchlistStore.load(account)
        }
      }
      if (!mutationActive.value) void reloadLots()
      if (accountChanged && !props.embedded) {
        if (account) accountsStore.setActiveAccount(account)
        emit('account-change', account)
      }
    }
  },
)

onMounted(() => {
  alive = true
  document.addEventListener('visibilitychange', handleVisibility)
  void loadPanel()
})

onBeforeUnmount(() => {
  alive = false
  generation += 1
  lotGeneration += 1
  clearTimer()
  releaseWatchSymbol()
  document.removeEventListener('visibilitychange', handleVisibility)
})
</script>

<template>
  <div :class="['detail-panel', { 'is-embedded': embedded }]">
    <template v-if="!isMobile">
      <section class="page-head detail-head">
        <div>
          <component :is="embedded ? 'h3' : 'h2'" class="detail-heading-title">
            {{ normalizedMarket }}:{{ normalizedSymbol }} {{ security?.name ?? '' }}
          </component>
          <p class="muted">
            批次、配对和止盈设置
            <template v-if="isFund">
              ·
              <span v-if="hasProxyBinding" class="proxy-inline-meta">{{ proxySummaryText }}</span>
              <span v-else>未绑定盘中跟踪标的</span>
            </template>
          </p>
        </div>
        <div class="toolbar">
          <el-select
            v-if="!embedded && matchingAccounts.length"
            v-model="accountId"
            aria-label="当前账户"
            style="width:170px"
          >
            <el-option v-for="account in matchingAccounts" :key="account.id" :value="account.id" :label="account.name" />
          </el-select>
          <el-button v-if="embedded" size="small" plain @click="emit('open-standalone')">独立页打开</el-button>
          <el-button v-if="isFund" :size="embedded ? 'small' : 'default'" :disabled="!security" @click="proxyDialogVisible = true">
            {{ hasProxyBinding ? '修改跟踪标的' : '绑定跟踪标的' }}
          </el-button>
          <el-button v-if="!embedded" @click="globalTargetVisible = true">全局止盈设置</el-button>
          <el-button
            :size="embedded ? 'small' : 'default'"
            :type="currentWatchlistItem?.hitBuyTarget ? 'danger' : currentWatchlistItem ? 'warning' : 'default'"
            :plain="!currentWatchlistItem?.hitBuyTarget"
            :loading="togglingWatch"
            :disabled="!security || !accountId"
            @click="handleWatchAction"
          >
            {{ watchButtonLabel }}
          </el-button>
          <el-button :size="embedded ? 'small' : 'default'" :disabled="!security" @click="securityTargetVisible = true">个股止盈设置</el-button>
          <el-button :size="embedded ? 'small' : 'default'" :disabled="!security || !accountId" @click="buyVisible = true">录入买入</el-button>
          <el-button :size="embedded ? 'small' : 'default'" type="danger" :disabled="!security || !accountId || !hasOpenLots" @click="openSellDrawer()">卖出配对</el-button>
        </div>
      </section>

      <el-alert v-if="pageError" :title="pageError" type="error" :closable="false" class="status-alert" />
      <el-alert
        v-if="security && !accountId"
        :title="`暂无 ${currency} 账户，请先在总览中创建同币种账户后记账。`"
        type="warning"
        :closable="false"
        class="status-alert"
      />

      <el-card class="panel">
        <template #header>
          <div class="panel-title chart-heading">
            <div class="chart-title-group">
              <span>
                {{
                  displayIntraday?.seriesKind === 'estimated_intraday'
                    ? '盘中估算净值走势'
                    : isFund
                      ? '近期单位净值走势'
                      : '当前日股价图'
                }}
                <span class="muted">
                  {{ displayIntraday?.tradingDate ?? '' }}{{
                    displayIntraday?.seriesKind === 'estimated_intraday'
                      ? (quotesStore.isLive ? ' · 盘中实时估算 · 实时推送' : ' · 盘中实时估算 · 每 3 秒刷新')
                      : isFund
                        ? ' · 已公布净值'
                        : (quotesStore.isLive ? ' · 实时推送' : ' · 每 3 秒刷新')
                  }}
                </span>
              </span>
              <el-button-group v-if="hasProxyBinding" size="small">
                <el-button :type="fundSeriesMode === 'auto' ? 'primary' : 'default'" @click="switchFundSeries('auto')">盘中估算</el-button>
                <el-button :type="fundSeriesMode === 'nav' ? 'primary' : 'default'" @click="switchFundSeries('nav')">历史净值</el-button>
              </el-button-group>
            </div>
            <QuoteBadge :quote="currentQuote" :currency="currency" />
          </div>
        </template>
        <el-alert v-if="marketError" :title="`行情刷新失败：${marketError}；保留最后有效数据作为过期参考。`" type="warning" :closable="false" class="status-alert" />
        <div class="muted">
          来源 {{ displayIntraday?.provider ?? '—' }} · 获取 {{ formatDateTime(displayIntraday?.fetchedAt) }}<span v-if="displayIntraday?.isStale || marketError"> · 行情已过期</span>
        </div>
        <IntradayChart :data="displayIntraday" />
      </el-card>

      <el-card class="panel">
        <template #header>
          <div class="panel-title lot-heading">
            <span>持仓批次 <span class="muted">已结清 {{ closedLotCount }} 批</span></span>
            <div class="toolbar">
              <el-switch v-model="preferences.mergeLotsByPrice" active-text="按买入价合并" />
              <el-switch v-model="preferences.includeClosed" active-text="显示已结清" />
              <el-button :disabled="!security || !accountId" :loading="rebuilding" @click="recalculate">重新计算</el-button>
            </div>
          </div>
        </template>
        <el-alert v-if="needsRefresh" type="warning" :closable="false" show-icon class="status-alert" title="需要刷新：交易流水已更正，当前持仓与收益尚未重新核算。">
          <p>刷新仅重新计算当前账户、个股与策略；已有显式止盈目标请在重算后核对。</p>
          <el-button type="warning" :loading="rebuilding" @click="recalculate">刷新持仓</el-button>
        </el-alert>
        <dl class="holding-summary" aria-label="持仓汇总">
          <div class="summary-item">
            <dt>{{ isFund ? '总份额' : '总持股数' }}</dt>
            <dd class="summary-value">{{ formatQuantity(displayHoldingSummary?.remainingQty, isFund) }}</dd>
          </div>
          <div class="summary-item">
            <dt>持仓成本</dt>
            <dd class="summary-value">{{ formatMoney(displayHoldingSummary?.remainingCost, currency) }}</dd>
          </div>
          <div class="summary-item">
            <dt>已实现收益</dt>
            <dd class="summary-value" :class="trendClass(displayHoldingSummary?.realizedPnl)">
              {{ formatMoney(displayHoldingSummary?.realizedPnl, currency) }}
            </dd>
          </div>
          <div class="summary-item">
            <dt>总浮盈</dt>
            <dd class="summary-value" :class="trendClass(displayHoldingSummary?.unrealizedPnl)">
              {{ formatMoney(displayHoldingSummary?.unrealizedPnl, currency) }}
            </dd>
            <p v-if="displayHoldingSummary && !hasRemainingHolding" class="summary-note">暂无持仓</p>
            <p v-else-if="displayHoldingSummary?.unrealizedPnl === null" class="summary-note">暂无有效行情</p>
            <p v-else-if="holdingSummaryStale" class="summary-note">参考浮盈 · 行情已过期</p>
          </div>
          <div class="summary-item" :title="lastSellTimeTitle">
            <dt>上次卖出价</dt>
            <dd :class="['summary-value', { 'is-muted': !displayHoldingSummary?.lastSellPrice }]">
              {{ lastSellPriceText }}
            </dd>
            <p v-if="displayHoldingSummary?.lastSellPrice" class="summary-note num">
              较上卖
              <span :class="['summary-diff', trendClass(displayHoldingSummary.vsLastSellPct)]">
                {{ formatSignedPercent(displayHoldingSummary.vsLastSellPct) }}
              </span>
            </p>
          </div>
        </dl>
        <div v-loading="loading || rebuilding">
          <LotTable :lots="displayLots" :include-closed="preferences.includeClosed" :merge-by-price="preferences.mergeLotsByPrice" :currency="currency" :is-fund="isFund" :quote="currentQuote" @set-target="addTarget" @sell="sellLot" />
        </div>
        <section v-if="preferences.includeClosed" class="settled-trade-section">
          <h3 class="panel-title">已结清买卖明细</h3>
          <p class="muted">显示已结清批次的原始买入和全部有效卖出；跨批次卖出仅展示本批数量、分摊净回款和已实现盈亏。</p>
          <SettledTradeTable :details="closedTradeDetails" :currency="currency" :is-fund="isFund" />
        </section>
      </el-card>
    </template>

    <template v-else>
      <section class="m-section m-detail-head">
        <div class="m-detail-top">
          <div class="m-detail-title-box">
            <h2 class="m-detail-title">{{ normalizedMarket }}:{{ normalizedSymbol }}</h2>
            <span v-if="security?.name" class="m-detail-name">{{ security.name }}</span>
          </div>
          <div class="m-detail-top-right">
            <QuoteBadge :quote="currentQuote" :currency="currency" compact />
            <button type="button" class="m-collapse-btn" :aria-expanded="mobileActionsOpen" @click="toggleMobileActions">
              {{ mobileActionsOpen ? '收起操作 ▴' : '操作 ▾' }}
            </button>
          </div>
        </div>
        <div v-if="mobileActionsOpen" class="m-detail-actions">
          <el-select v-if="matchingAccounts.length" v-model="accountId" aria-label="当前账户" size="small" class="m-detail-account">
            <el-option v-for="account in matchingAccounts" :key="account.id" :value="account.id" :label="account.name" />
          </el-select>
          <el-button size="small" :disabled="!security || !accountId" @click="buyVisible = true">录入买入</el-button>
          <el-button size="small" type="danger" :disabled="!security || !accountId || !hasOpenLots" @click="openSellDrawer()">卖出配对</el-button>
          <el-button
            size="small"
            :type="currentWatchlistItem?.hitBuyTarget ? 'danger' : currentWatchlistItem ? 'warning' : 'default'"
            :plain="!currentWatchlistItem?.hitBuyTarget"
            :loading="togglingWatch"
            :disabled="!security || !accountId"
            @click="handleWatchAction"
          >
            {{ watchButtonLabel }}
          </el-button>
          <el-button v-if="isFund" size="small" :disabled="!security" @click="proxyDialogVisible = true">{{ hasProxyBinding ? '修改跟踪' : '绑定跟踪' }}</el-button>
          <el-button size="small" :disabled="!security" @click="securityTargetVisible = true">个股止盈</el-button>
          <el-button size="small" @click="globalTargetVisible = true">全局止盈</el-button>
        </div>
      </section>

      <el-alert v-if="pageError" :title="pageError" type="error" :closable="false" class="status-alert" />
      <el-alert v-if="security && !accountId" :title="`暂无 ${currency} 账户，请先在总览中创建同币种账户后记账。`" type="warning" :closable="false" class="status-alert" />

      <section class="m-section m-chart-box">
        <button type="button" class="m-section-head m-collapse-head" :aria-expanded="mobileChartOpen" @click="toggleMobileChart">
          <span>{{ displayIntraday?.seriesKind === 'estimated_intraday' ? '盘中估算分时' : isFund ? '近期净值' : '日内分时' }} <span class="m-section-sub">{{ displayIntraday?.tradingDate ?? '' }}</span></span>
          <span class="m-section-sub">{{ displayIntraday?.provider ?? '—' }}<span v-if="displayIntraday?.isStale || marketError"> · 已过期</span> · {{ mobileChartOpen ? '收起 ▴' : '展开 ▾' }}</span>
        </button>
        <template v-if="mobileChartOpen">
          <div v-if="hasProxyBinding" class="m-series-toggle">
            <span class="m-section-sub">{{ proxySummaryText }}</span>
            <el-button-group size="small">
              <el-button :type="fundSeriesMode === 'auto' ? 'primary' : 'default'" @click="switchFundSeries('auto')">盘中估算</el-button>
              <el-button :type="fundSeriesMode === 'nav' ? 'primary' : 'default'" @click="switchFundSeries('nav')">历史净值</el-button>
            </el-button-group>
          </div>
          <div v-if="marketError" class="m-compact-note is-warning">行情刷新失败：{{ marketError }}；保留最后有效数据作为过期参考。</div>
          <div class="m-chart-body"><IntradayChart :data="displayIntraday" /></div>
        </template>
      </section>

      <section class="m-section">
        <div class="m-section-head">
          <span>持仓批次 <span class="m-section-sub">已结清 {{ closedLotCount }} 批</span></span>
          <div class="m-lot-tools">
            <el-switch v-model="preferences.mergeLotsByPrice" size="small" active-text="按价合并" />
            <el-switch v-model="preferences.includeClosed" size="small" active-text="已结清" />
            <el-button size="small" :disabled="!security || !accountId" :loading="rebuilding" @click="recalculate">重算</el-button>
          </div>
        </div>
        <div v-if="needsRefresh" class="m-refresh-banner">
          <span>交易流水已更正，当前持仓与收益尚未重新核算。</span>
          <el-button size="small" type="warning" :loading="rebuilding" @click="recalculate">刷新持仓</el-button>
        </div>
        <dl class="holding-summary m-holding-summary" aria-label="持仓汇总">
          <div class="summary-item">
            <dt>{{ isFund ? '总份额' : '总持股数' }}</dt>
            <dd class="summary-value">{{ formatQuantity(displayHoldingSummary?.remainingQty, isFund) }}</dd>
          </div>
          <div class="summary-item">
            <dt>持仓成本</dt>
            <dd class="summary-value">{{ formatMoney(displayHoldingSummary?.remainingCost, currency) }}</dd>
          </div>
          <div class="summary-item">
            <dt>已实现收益</dt>
            <dd class="summary-value" :class="trendClass(displayHoldingSummary?.realizedPnl)">{{ formatMoney(displayHoldingSummary?.realizedPnl, currency) }}</dd>
          </div>
          <div class="summary-item">
            <dt>总浮盈</dt>
            <dd class="summary-value" :class="trendClass(displayHoldingSummary?.unrealizedPnl)">{{ formatMoney(displayHoldingSummary?.unrealizedPnl, currency) }}</dd>
            <p v-if="displayHoldingSummary && !hasRemainingHolding" class="summary-note">暂无持仓</p>
            <p v-else-if="displayHoldingSummary?.unrealizedPnl === null" class="summary-note">暂无有效行情</p>
            <p v-else-if="holdingSummaryStale" class="summary-note">参考浮盈 · 行情已过期</p>
          </div>
          <div class="summary-item summary-item--full" :title="lastSellTimeTitle">
            <dt>上次卖出价</dt>
            <dd class="summary-value summary-inline-val num">
              <span :class="{ 'is-muted': !displayHoldingSummary?.lastSellPrice }">{{ lastSellPriceText }}</span>
              <span v-if="displayHoldingSummary?.lastSellPrice" :class="['summary-inline-diff', trendClass(displayHoldingSummary.vsLastSellPct)]">
                较上卖 {{ formatSignedPercent(displayHoldingSummary.vsLastSellPct) }}
              </span>
            </dd>
          </div>
        </dl>
        <div v-loading="loading || rebuilding">
          <LotTable :lots="displayLots" :include-closed="preferences.includeClosed" :merge-by-price="preferences.mergeLotsByPrice" :currency="currency" :is-fund="isFund" :quote="currentQuote" @set-target="addTarget" @sell="sellLot" />
        </div>
      </section>

      <section v-if="preferences.includeClosed" class="m-section">
        <div class="m-section-head"><span>已结清买卖明细</span></div>
        <div class="m-compact-note is-info">跨批次卖出仅展示本批数量、分摊净回款和已实现盈亏。</div>
        <SettledTradeTable :details="closedTradeDetails" :currency="currency" :is-fund="isFund" />
      </section>
    </template>

    <BuyTradeDialog v-if="security" v-model="buyVisible" :security-id="security.id" :account-id="accountId" :currency="currency" :is-fund="isFund" @saved="reloadLots(true)" />
    <SellMatchDrawer v-if="security" v-model="sellVisible" :security-id="security.id" :account-id="accountId" :currency="currency" :is-fund="isFund" :lots="lots" :initial-qty="sellInitialQty" @saved="reloadLots(true)" />
    <ProfitTargetDialog v-if="selectedLot" v-model="targetVisible" :lot-id="selectedLot.id" :lot="selectedLot" :is-fund="isFund" @saved="reloadLots(true)" />
    <ProfitSettingDialog v-model="globalTargetVisible" @saved="reloadLots(true)" />
    <ProfitSettingDialog v-if="security" v-model="securityTargetVisible" :security-id="security.id" @saved="reloadLots(true)" />
    <FundProxyDialog v-if="security && isFund" v-model="proxyDialogVisible" :security="security" @saved="handleProxySaved" />
    <WatchlistSettingDialog
      v-if="security"
      v-model="watchlistDialogVisible"
      :security-id="security.id"
      :account-id="accountId"
      :symbol-label="`${security.market}:${security.symbol}`"
      :security-name="security.name"
      :currency="currency"
      :is-fund="isFund"
      :existing="currentWatchlistItem"
      :last-sell-price="displayHoldingSummary?.lastSellPrice ?? currentWatchlistItem?.lastSellPrice"
      :current-price="currentQuote?.lastPrice"
    />
  </div>
</template>

<style scoped>
.detail-panel { min-width: 0; }
.detail-head { gap: 12px; flex-wrap: wrap; }
.detail-panel.is-embedded .detail-head { margin-bottom: 14px; }
.detail-heading-title { margin: 0 0 6px; font-size: 25px; }
.detail-panel.is-embedded .detail-heading-title { font-size: 20px; }
.detail-head .toolbar { flex-wrap: wrap; }
.chart-heading, .lot-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.chart-title-group { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.proxy-inline-meta { color: #2563eb; font-weight: 500; }
.m-series-toggle { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 10px 2px; flex-wrap: wrap; }
.holding-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  margin: 0 0 18px;
  border-top: 1px solid var(--el-border-color-lighter);
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.detail-panel.is-embedded .holding-summary {
  grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
}
.summary-item { min-width: 0; padding: 12px 14px; border-left: 1px solid var(--el-border-color-lighter); }
.summary-item:first-child { border-left: 0; padding-left: 0; }
.summary-item dt { margin-bottom: 5px; color: var(--el-text-color-secondary); font-size: 12px; }
.summary-value { margin: 0; overflow-wrap: anywhere; font-size: 16px; font-weight: 600; }
.summary-value.is-muted, .is-muted { color: #94a3b8; font-weight: 500; }
.summary-note { margin: 4px 0 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.4; }
.summary-diff { font-weight: 600; margin-left: 3px; }
.settled-trade-section { margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--el-border-color-lighter); }
.settled-trade-section h3 { margin: 0 0 8px; font-size: 16px; }
@media (max-width: 768px) {
  .holding-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-bottom: 0; border-top: 0; }
  .summary-item { padding: 8px 10px; }
  .summary-item:nth-child(odd) { border-left: 0; }
  .summary-item:nth-child(n + 3) { border-top: 1px solid var(--el-border-color-lighter); }
  .summary-item--full { grid-column: 1 / -1; border-left: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .summary-item--full dt { margin-bottom: 0; }
  .summary-inline-val { display: inline-flex; align-items: baseline; gap: 8px; font-size: 14px; }
  .summary-inline-diff { font-size: 12px; font-weight: 600; }
  .summary-value { font-size: 15px; }
  .summary-note { font-size: 11px; }
}
.m-detail-head { padding: 8px 10px; }
.m-detail-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.m-detail-top-right { display: flex; align-items: flex-start; gap: 8px; flex-shrink: 0; }
.m-collapse-btn { height: 24px; padding: 0 7px; border: 1px solid #dbe2ec; border-radius: 5px; background: #f8fafc; color: #334155; font-size: 11px; font-weight: 600; cursor: pointer; white-space: nowrap; flex-shrink: 0; }
.m-collapse-head { width: 100%; border: 0; background: transparent; text-align: left; cursor: pointer; }
.m-detail-title-box { min-width: 0; }
.m-detail-title { margin: 0; font-size: 16px; line-height: 1.25; }
.m-detail-name { display: block; font-size: 12px; color: #64748b; margin-top: 2px; }
.m-detail-actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 8px; padding-top: 8px; border-top: 1px dashed #edf0f5; }
.m-detail-account { width: 124px; }
.m-chart-body { padding: 4px 6px 6px; }
.m-lot-tools { display: flex; align-items: center; gap: 6px; }
.m-refresh-banner { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 10px; background: #fdf6ec; border-bottom: 1px solid #f5e6c8; font-size: 11px; color: #a66a14; }
</style>
