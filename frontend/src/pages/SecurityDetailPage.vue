<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Account, Currency, DashboardPosition, IntradayRead, Lot, LotsRead, LotsSummary, PositionRefreshScope, Quote, Security, SettledTradeDetail } from '../api/types'
import LotTable from '../components/LotTable.vue'
import SettledTradeTable from '../components/SettledTradeTable.vue'
import BuyTradeDialog from '../components/BuyTradeDialog.vue'
import SellMatchDrawer from '../components/SellMatchDrawer.vue'
import ProfitTargetDialog from '../components/ProfitTargetDialog.vue'
import ProfitSettingDialog from '../components/ProfitSettingDialog.vue'
import IntradayChart from '../components/IntradayChart.vue'
import QuoteBadge from '../components/QuoteBadge.vue'
import { usePreferencesStore } from '../stores/preferences'
import { useLedgerStore } from '../stores/ledger'
import { formatDateTime, formatMoney, formatQuantity, formatSignedPercent, isFundSecurity, priceDigits, trendClass } from '../utils/format'
import { isPositionRefreshing, refreshPosition } from '../utils/recalculate'
import { useViewport } from '../utils/viewport'

const route = useRoute()
const router = useRouter()
const preferences = usePreferencesStore()
const ledger = useLedgerStore()
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
const targetVisible = ref(false)
const globalTargetVisible = ref(false)
const securityTargetVisible = ref(false)
const mobileActionsOpen = ref(false)
const mobileChartOpen = ref(false)
const selectedLotId = ref('')
const selectedLot = computed(() => lots.value.find((lot) => lot.id === selectedLotId.value))
const isFund = computed(() => isFundSecurity(security.value ?? { market: String(route.params.market ?? '') }))
const currency = computed<Currency>(() => security.value?.currency ?? (security.value?.market === 'US' ? 'USD' : 'CNY'))
const matchingAccounts = computed(() => accounts.value.filter((account) => account.currency === currency.value))
const requestedStrategyId = computed(() => typeof route.query.strategyId === 'string' ? route.query.strategyId : undefined)
const refreshScope = computed<PositionRefreshScope | null>(() => security.value && accountId.value ? { accountId: accountId.value, securityId: security.value.id, strategyId: resolvedStrategyId.value ?? requestedStrategyId.value, market: security.value.market, symbol: security.value.symbol, name: security.value.name } : null)
const rebuilding = computed(() => refreshScope.value ? isPositionRefreshing(refreshScope.value) : false)
const currentQuote = computed(() => {
  const quote = lotQuote.value ?? intraday.value?.quote
  return quote && (marketError.value || intraday.value?.isStale) ? { ...quote, isStale: true } : quote
})
/** 判断有价汇总是否只能作为过期参考展示。 */
const holdingSummaryStale = computed(() => holdingSummary.value?.unrealizedPnl !== null && holdingSummary.value?.unrealizedPnl !== undefined && Boolean(currentQuote.value?.isStale))
const hasOpenLots = computed(() => lots.value.some((lot) => lot.status === 'OPEN'))
/** 判断服务端汇总是否包含仍有剩余数量的未结清批次。 */
const hasRemainingHolding = computed(() => Boolean(holdingSummary.value && /[1-9]/.test(holdingSummary.value.remainingQty)))
/** 按当前涨幅降序排列活动仓位，无有效行情的持仓排在末尾，供详情页快速切换。 */
const activePositions = computed<DashboardPosition[]>(() => {
  return [...ledger.positions]
    .filter((position) => Boolean(position.market && position.symbol))
    .sort((left, right) => {
      const leftVal = Number(left.quote?.changePct)
      const rightVal = Number(right.quote?.changePct)
      const leftValid = Number.isFinite(leftVal)
      const rightValid = Number.isFinite(rightVal)
      if (!leftValid && !rightValid) return 0
      if (!leftValid) return 1
      if (!rightValid) return -1
      return rightVal - leftVal
    })
})

/** 判断活动仓位条目是否为当前详情页正在查看的证券。 */
function isCurrentPosition(position: DashboardPosition): boolean {
  return (
    position.market?.toUpperCase() === String(route.params.market).toUpperCase()
    && position.symbol?.toUpperCase() === String(route.params.symbol).toUpperCase()
  )
}

/** 从详情页活动仓位列表切换到目标个股，并保留快照账户与策略范围。 */
function switchPosition(position: DashboardPosition): void {
  if (!position.market || !position.symbol || isCurrentPosition(position)) return
  void router.push({
    path: `/security/${position.market}/${position.symbol}`,
    query: {
      accountId: ledger.dashboard?.accountId ?? (accountId.value || undefined),
      strategyId: position.strategyId,
    },
  })
}

/** 仅在尚未缓存首页快照时静默拉取缓存活动仓位，不阻塞详情页主加载。 */
function ensureActivePositions(): void {
  if (ledger.dashboard) return
  void ledger.loadDashboard({
    accountId: accountId.value || undefined,
    includeClosed: false,
    mode: 'cache',
  })
}

/** 切换移动端详情页快捷操作栏的展开或收起状态。 */
function toggleMobileActions(): void {
  mobileActionsOpen.value = !mobileActionsOpen.value
}

/** 切换移动端详情页日内分时图区域的展开或收起状态。 */
function toggleMobileChart(): void {
  mobileChartOpen.value = !mobileChartOpen.value
}
let alive = false
let generation = 0
let lotGeneration = 0
let dataGeneration = 0
let pollTimer: number | null = null
let marketFlight: { generation: number; promise: Promise<void> } | null = null

/** 清理轮询，防止离页、隐藏或重新加载后遗留计时器。 */
function clearTimer() {
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = null
}

/** 统一替换后端返回的批次估值，不在浏览器重算账务。 */
function applyLots(result: LotsRead) {
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
  if (!currentSecurity || !account) { lots.value = []; holdingSummary.value = null; closedTradeDetails.value = []; needsRefresh.value = false; return false }
  let result: LotsRead
  try {
    result = await api.lots(currentSecurity.id, includeClosed, account, strategy)
  } catch (cause) {
    if (alive && currentGeneration === generation && currentDataGeneration === dataGeneration && request === lotGeneration && account === accountId.value && strategy === requestedStrategyId.value && includeClosed === preferences.includeClosed) holdingSummary.value = null
    throw cause
  }
  if (!alive || currentGeneration !== generation || currentDataGeneration !== dataGeneration || request !== lotGeneration || account !== accountId.value || strategy !== requestedStrategyId.value || includeClosed !== preferences.includeClosed) return false
  if ((result.accountId && result.accountId !== account) || (result.strategyId && strategy && result.strategyId !== strategy)) {
    holdingSummary.value = null
    throw new Error('返回的持仓范围与当前账户、策略不一致')
  }
  applyLots(result)
  return true
}

/** 每次行情请求完成三秒后再刷新；场外基金净值按日公布，不开启 3 秒轮询。 */
function schedulePoll() {
  clearTimer()
  if (!alive || document.hidden || !security.value || mutationActive.value || isFund.value) return
  pollTimer = window.setTimeout(() => { pollTimer = null; void refreshMarket() }, 3000)
}

/** 先获取真实日内行情，再读取同份缓存计算的批次浮盈，失败保留上一份数据。 */
async function refreshMarket() {
  if (!alive || document.hidden || !security.value || mutationActive.value) return
  if (marketFlight?.generation === generation) return marketFlight.promise
  const currentGeneration = generation
  const currentDataGeneration = dataGeneration
  const id = security.value.id
  /** 为当前证券代次执行一次串行行情与批次刷新。 */
  const execute = async () => {
    try {
      const result = await api.intraday(id)
      if (!alive || currentGeneration !== generation || currentDataGeneration !== dataGeneration || mutationActive.value) return
      await loadLots()
      if (!alive || currentGeneration !== generation || currentDataGeneration !== dataGeneration) return
      intraday.value = result
      marketError.value = result.error ?? ''
    } catch (cause) {
      if (alive && currentGeneration === generation && currentDataGeneration === dataGeneration) marketError.value = cause instanceof Error ? cause.message : '行情刷新失败'
    } finally {
      if (marketFlight?.generation === currentGeneration && currentDataGeneration === dataGeneration) marketFlight = null
      if (alive && currentGeneration === generation && currentDataGeneration === dataGeneration) schedulePoll()
    }
  }
  const promise = execute()
  marketFlight = { generation: currentGeneration, promise }
  return promise
}

/** 路由切换时重新定位证券和同币种账户，并使旧证券的所有响应失效。 */
async function loadPage() {
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
  targetVisible.value = false
  securityTargetVisible.value = false
  try {
    const market = String(route.params.market).toUpperCase()
    const symbol = String(route.params.symbol).toUpperCase()
    const [found, availableAccounts] = await Promise.all([api.securities(symbol), api.accounts()])
    if (!alive || request !== generation) return
    security.value = found.find((item) => item.market.toUpperCase() === market && item.symbol.toUpperCase() === symbol) ?? null
    accounts.value = availableAccounts
    if (!security.value) throw new Error('找不到该证券，请先在总览中查找并添加')
    const requestedAccount = typeof route.query.accountId === 'string' ? route.query.accountId : undefined
    if (requestedAccount && !matchingAccounts.value.some((account) => account.id === requestedAccount)) throw new Error('指定账户不存在或与当前证券币种不匹配')
    if (requestedAccount) accountId.value = requestedAccount
    else if (!matchingAccounts.value.some((account) => account.id === accountId.value)) accountId.value = matchingAccounts.value[0]?.id ?? ''
    await loadLots()
    if (alive && request === generation) void refreshMarket()
  } catch (cause) { if (request === generation) pageError.value = cause instanceof Error ? cause.message : '加载个股失败' }
  finally { if (request === generation) loading.value = false }
}

/** 操作成功或显示条件改变后，更新批次并保留交易记录可用性。 */
async function reloadLots() {
  invalidateReads()
  try { await loadLots(true) }
  catch (cause) { ElMessage.error(cause instanceof Error ? cause.message : '刷新批次失败') }
  finally { if (!mutationActive.value) schedulePoll() }
}

/** 写入前后废弃批次和行情旧响应，并同步失效首页快照读取。 */
function invalidateReads() {
  dataGeneration += 1
  lotGeneration += 1
  marketFlight = null
  clearTimer()
  ledger.invalidateDashboard()
}

/** 原重算和待刷新入口共享确认、防重及成功后的数据读取流程。 */
async function recalculate() {
  const scope = refreshScope.value
  if (!scope) return
  const request = generation
  let started = false
  await refreshPosition(scope, {
    isCurrent: () => alive && request === generation && scope.accountId === accountId.value && scope.securityId === security.value?.id && scope.strategyId === refreshScope.value?.strategyId,
    invalidate: () => { if (!started) { started = true; activeMutations.value += 1 }; invalidateReads() },
    reload: () => loadLots(true),
    settled: () => {
      activeMutations.value -= 1
      if (!alive) return
      if (request !== generation && !mutationActive.value && !loading.value) void reloadLots()
      else schedulePoll()
    },
  })
}

/** 将设置操作锚定至本行批次，不在列表外生成批次按钮。 */
function addTarget(lot: Lot) { selectedLotId.value = lot.id; targetVisible.value = true }

/** 标签页恢复可见立即刷新，隐藏时不再安排请求。 */
function handleVisibility() { clearTimer(); if (!document.hidden) void refreshMarket() }

watch(() => [route.params.market, route.params.symbol, route.query.accountId, route.query.strategyId], () => { if (alive) void loadPage() })
watch(() => [accountId.value, preferences.includeClosed] as const, ([account], previous) => {
  // 查询范围或开关改变后立即清空旧明细，失败与乱序响应也不能恢复其他范围的数据。
  closedTradeDetails.value = []
  if (alive && !loading.value) {
    if (account !== previous[0]) {
      generation += 1
      resolvedStrategyId.value = undefined
      lots.value = []
      holdingSummary.value = null
      needsRefresh.value = false
    }
    if (!mutationActive.value) void reloadLots()
  }
})
onMounted(() => { alive = true; document.addEventListener('visibilitychange', handleVisibility); ensureActivePositions(); void loadPage() })
onBeforeUnmount(() => { alive = false; generation += 1; lotGeneration += 1; clearTimer(); document.removeEventListener('visibilitychange', handleVisibility) })
</script>

<template>
  <template v-if="!isMobile">
  <div :class="['detail-layout', { 'detail-layout--with-aside': activePositions.length > 0 }]">
    <aside v-if="activePositions.length" class="detail-pos-aside" aria-label="活动仓位快速切换">
      <div class="detail-pos-aside-head">
        <span>活动仓位</span>
        <span class="muted">{{ activePositions.length }}</span>
      </div>
      <div class="detail-pos-list">
        <button
          v-for="pos in activePositions"
          :key="`${pos.securityId}|${pos.strategyId}`"
          type="button"
          :class="['detail-pos-item', { 'is-active': isCurrentPosition(pos) }]"
          @click="switchPosition(pos)"
        >
          <div class="detail-pos-left">
            <strong class="detail-pos-name">{{ pos.name || pos.symbol }}</strong>
            <span class="detail-pos-code">{{ pos.market }}:{{ pos.symbol }}</span>
          </div>
          <div class="detail-pos-right num">
            <span class="detail-pos-price">{{ pos.quote?.lastPrice ? formatMoney(pos.quote.lastPrice, pos.currency ?? currency, priceDigits(pos, 3)) : '—' }}</span>
            <span :class="['detail-pos-chg', trendClass(pos.quote?.changePct)]">{{ formatSignedPercent(pos.quote?.changePct) }}</span>
          </div>
        </button>
      </div>
    </aside>
    <div class="detail-main">
  <section class="page-head detail-head">
    <div><h2>{{ route.params.market }}:{{ route.params.symbol }} {{ security?.name ?? '' }}</h2><p class="muted">批次、配对和止盈设置</p></div>
    <div class="toolbar"><el-select v-if="matchingAccounts.length" v-model="accountId" aria-label="当前账户" style="width:170px"><el-option v-for="account in matchingAccounts" :key="account.id" :value="account.id" :label="account.name" /></el-select><el-button @click="globalTargetVisible = true">全局止盈设置</el-button><el-button :disabled="!security" @click="securityTargetVisible = true">个股止盈设置</el-button><el-button :disabled="!security || !accountId" @click="buyVisible = true">录入买入</el-button><el-button type="danger" :disabled="!security || !accountId || !hasOpenLots" @click="sellVisible = true">卖出配对</el-button></div>
  </section>
  <el-alert v-if="pageError" :title="pageError" type="error" :closable="false" class="status-alert" />
  <el-alert v-if="security && !accountId" :title="`暂无 ${currency} 账户，请先在总览中创建同币种账户后记账。`" type="warning" :closable="false" class="status-alert" />
  <el-card class="panel">
    <template #header><div class="panel-title chart-heading"><span>{{ isFund ? '近期单位净值走势' : '当前日股价图' }} <span class="muted">{{ intraday?.tradingDate ?? '' }}{{ isFund ? ' · 已公布净值' : ' · 每 3 秒刷新' }}</span></span><QuoteBadge :quote="currentQuote" :currency="currency" /></div></template>
    <el-alert v-if="marketError" :title="`行情刷新失败：${marketError}；保留最后有效数据作为过期参考。`" type="warning" :closable="false" class="status-alert" />
    <div class="muted">来源 {{ intraday?.provider ?? '—' }} · 获取 {{ formatDateTime(intraday?.fetchedAt) }}<span v-if="intraday?.isStale || marketError"> · 行情已过期</span></div>
    <IntradayChart :data="intraday" />
  </el-card>
  <el-card class="panel">
    <template #header><div class="panel-title lot-heading"><span>持仓批次 <span class="muted">已结清 {{ closedLotCount }} 批</span></span><div class="toolbar"><el-switch v-model="preferences.includeClosed" active-text="显示已结清" /><el-button :disabled="!security || !accountId" :loading="rebuilding" @click="recalculate">重新计算</el-button></div></div></template>
    <el-alert v-if="needsRefresh" type="warning" :closable="false" show-icon class="status-alert" title="需要刷新：交易流水已更正，当前持仓与收益尚未重新核算。">
      <p>刷新仅重新计算当前账户、个股与策略；已有显式止盈目标请在重算后核对。</p>
      <el-button type="warning" :loading="rebuilding" @click="recalculate">刷新持仓</el-button>
    </el-alert>
    <dl class="holding-summary" aria-label="持仓汇总">
      <div class="summary-item">
        <dt>{{ isFund ? '总份额' : '总持股数' }}</dt>
        <dd class="summary-value">{{ formatQuantity(holdingSummary?.remainingQty, isFund) }}</dd>
      </div>
      <div class="summary-item">
        <dt>持仓成本</dt>
        <dd class="summary-value">{{ formatMoney(holdingSummary?.remainingCost, currency) }}</dd>
      </div>
      <div class="summary-item">
        <dt>已实现收益</dt>
        <dd class="summary-value" :class="trendClass(holdingSummary?.realizedPnl)">{{ formatMoney(holdingSummary?.realizedPnl, currency) }}</dd>
      </div>
      <div class="summary-item">
        <dt>总浮盈</dt>
        <dd class="summary-value" :class="trendClass(holdingSummary?.unrealizedPnl)">{{ formatMoney(holdingSummary?.unrealizedPnl, currency) }}</dd>
        <p v-if="holdingSummary && !hasRemainingHolding" class="summary-note">暂无持仓</p>
        <p v-else-if="holdingSummary?.unrealizedPnl === null" class="summary-note">暂无有效行情</p>
        <p v-else-if="holdingSummaryStale" class="summary-note">参考浮盈 · 行情已过期</p>
      </div>
    </dl>
    <div v-loading="loading || rebuilding"><LotTable :lots="lots" :include-closed="preferences.includeClosed" :currency="currency" :is-fund="isFund" :quote="currentQuote" @set-target="addTarget" /></div>
    <section v-if="preferences.includeClosed" class="settled-trade-section">
      <h3 class="panel-title">已结清买卖明细</h3>
      <p class="muted">显示已结清批次的原始买入和全部有效卖出；跨批次卖出仅展示本批数量、分摊净回款和已实现盈亏。</p>
      <SettledTradeTable :details="closedTradeDetails" :currency="currency" :is-fund="isFund" />
    </section>
  </el-card>
    </div>
  </div>
  </template>

  <template v-else>
    <nav v-if="activePositions.length" class="m-pos-strip-wrap" aria-label="活动仓位快速切换">
      <div :class="['m-pos-strip', { 'm-pos-strip--two-rows': activePositions.length > 2 }]">
        <button
          v-for="pos in activePositions"
          :key="`${pos.securityId}|${pos.strategyId}`"
          type="button"
          :class="['m-pos-chip', { 'is-active': isCurrentPosition(pos) }]"
          @click="switchPosition(pos)"
        >
          <div class="m-pos-chip-top">
            <strong class="m-pos-chip-name">{{ pos.name || pos.symbol }}</strong>
            <span :class="['m-pos-chip-chg', 'num', trendClass(pos.quote?.changePct)]">{{ formatSignedPercent(pos.quote?.changePct) }}</span>
          </div>
          <div class="m-pos-chip-sub num">
            <span>{{ pos.symbol }}</span>
            <span>{{ pos.quote?.lastPrice ? formatMoney(pos.quote.lastPrice, pos.currency ?? currency, priceDigits(pos, 3)) : '—' }}</span>
          </div>
        </button>
      </div>
    </nav>

    <section class="m-section m-detail-head">
      <div class="m-detail-top">
        <div class="m-detail-title-box">
          <h2 class="m-detail-title">{{ route.params.market }}:{{ route.params.symbol }}</h2>
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
        <el-button size="small" type="danger" :disabled="!security || !accountId || !hasOpenLots" @click="sellVisible = true">卖出配对</el-button>
        <el-button size="small" :disabled="!security" @click="securityTargetVisible = true">个股止盈</el-button>
        <el-button size="small" @click="globalTargetVisible = true">全局止盈</el-button>
      </div>
    </section>

    <el-alert v-if="pageError" :title="pageError" type="error" :closable="false" class="status-alert" />
    <el-alert v-if="security && !accountId" :title="`暂无 ${currency} 账户，请先在总览中创建同币种账户后记账。`" type="warning" :closable="false" class="status-alert" />

    <section class="m-section m-chart-box">
      <button type="button" class="m-section-head m-collapse-head" :aria-expanded="mobileChartOpen" @click="toggleMobileChart">
        <span>{{ isFund ? '近期净值' : '日内分时' }} <span class="m-section-sub">{{ intraday?.tradingDate ?? '' }}</span></span>
        <span class="m-section-sub">{{ intraday?.provider ?? '—' }}<span v-if="intraday?.isStale || marketError"> · 已过期</span> · {{ mobileChartOpen ? '收起 ▴' : '展开 ▾' }}</span>
      </button>
      <template v-if="mobileChartOpen">
        <div v-if="marketError" class="m-compact-note is-warning">行情刷新失败：{{ marketError }}；保留最后有效数据作为过期参考。</div>
        <div class="m-chart-body"><IntradayChart :data="intraday" /></div>
      </template>
    </section>

    <section class="m-section">
      <div class="m-section-head">
        <span>持仓批次 <span class="m-section-sub">已结清 {{ closedLotCount }} 批</span></span>
        <div class="m-lot-tools">
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
          <dd class="summary-value">{{ formatQuantity(holdingSummary?.remainingQty, isFund) }}</dd>
        </div>
        <div class="summary-item">
          <dt>持仓成本</dt>
          <dd class="summary-value">{{ formatMoney(holdingSummary?.remainingCost, currency) }}</dd>
        </div>
        <div class="summary-item">
          <dt>已实现收益</dt>
          <dd class="summary-value" :class="trendClass(holdingSummary?.realizedPnl)">{{ formatMoney(holdingSummary?.realizedPnl, currency) }}</dd>
        </div>
        <div class="summary-item">
          <dt>总浮盈</dt>
          <dd class="summary-value" :class="trendClass(holdingSummary?.unrealizedPnl)">{{ formatMoney(holdingSummary?.unrealizedPnl, currency) }}</dd>
          <p v-if="holdingSummary && !hasRemainingHolding" class="summary-note">暂无持仓</p>
          <p v-else-if="holdingSummary?.unrealizedPnl === null" class="summary-note">暂无有效行情</p>
          <p v-else-if="holdingSummaryStale" class="summary-note">参考浮盈 · 行情已过期</p>
        </div>
      </dl>
      <div v-loading="loading || rebuilding"><LotTable :lots="lots" :include-closed="preferences.includeClosed" :currency="currency" :is-fund="isFund" :quote="currentQuote" @set-target="addTarget" /></div>
    </section>

    <section v-if="preferences.includeClosed" class="m-section">
      <div class="m-section-head"><span>已结清买卖明细</span></div>
      <div class="m-compact-note is-info">跨批次卖出仅展示本批数量、分摊净回款和已实现盈亏。</div>
      <SettledTradeTable :details="closedTradeDetails" :currency="currency" :is-fund="isFund" />
    </section>
  </template>

  <BuyTradeDialog v-if="security" v-model="buyVisible" :security-id="security.id" :account-id="accountId" :currency="currency" :is-fund="isFund" @saved="reloadLots" />
  <SellMatchDrawer v-if="security" v-model="sellVisible" :security-id="security.id" :account-id="accountId" :currency="currency" :is-fund="isFund" :lots="lots" @saved="reloadLots" />
  <ProfitTargetDialog v-if="selectedLot" v-model="targetVisible" :lot-id="selectedLot.id" :lot="selectedLot" :is-fund="isFund" @saved="reloadLots" />
  <ProfitSettingDialog v-model="globalTargetVisible" @saved="reloadLots" />
  <ProfitSettingDialog v-if="security" v-model="securityTargetVisible" :security-id="security.id" @saved="reloadLots" />
</template>

<style scoped>
.detail-head { gap: 16px; flex-wrap: wrap; }
.detail-head .toolbar { flex-wrap: wrap; }
.chart-heading, .lot-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.holding-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0 0 18px; border-top: 1px solid var(--el-border-color-lighter); border-bottom: 1px solid var(--el-border-color-lighter); }
.summary-item { min-width: 0; padding: 12px 16px; border-left: 1px solid var(--el-border-color-lighter); }
.summary-item:first-child { border-left: 0; padding-left: 0; }
.summary-item dt { margin-bottom: 5px; color: var(--el-text-color-secondary); font-size: 12px; }
.summary-value { margin: 0; overflow-wrap: anywhere; font-size: 17px; font-weight: 600; }
.summary-note { margin: 4px 0 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.4; }
.settled-trade-section { margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--el-border-color-lighter); }
.settled-trade-section h3 { margin: 0 0 8px; font-size: 16px; }
@media (max-width: 768px) {
  .holding-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-bottom: 0; border-top: 0; }
  .summary-item { padding: 8px 10px; }
  .summary-item:nth-child(odd) { border-left: 0; }
  .summary-item:nth-child(n + 3) { border-top: 1px solid var(--el-border-color-lighter); }
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
.detail-layout { display: block; }
.detail-layout--with-aside { display: flex; align-items: flex-start; gap: 18px; }
.detail-pos-aside { flex: 0 0 220px; width: 220px; position: sticky; top: 16px; max-height: calc(100vh - 120px); display: flex; flex-direction: column; background: #fff; border-radius: 8px; box-shadow: 0 4px 18px #1822300b; overflow: hidden; }
.detail-pos-aside-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; border-bottom: 1px solid #edf0f5; font-size: 13px; font-weight: 700; color: #182230; }
.detail-pos-list { overflow-y: auto; display: flex; flex-direction: column; }
.detail-pos-item { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; padding: 10px 12px; border: 0; border-left: 3px solid transparent; border-bottom: 1px solid #f1f4f9; background: #fff; text-align: left; cursor: pointer; transition: background-color 0.15s ease; }
.detail-pos-item:last-child { border-bottom: 0; }
.detail-pos-item:hover { background: #f8fafd; }
.detail-pos-item.is-active { background: #eff6ff; border-left-color: #2563eb; }
.detail-pos-left { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.detail-pos-name { font-size: 13px; color: #182230; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.detail-pos-code { font-size: 11px; color: #7d899b; }
.detail-pos-right { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; flex-shrink: 0; }
.detail-pos-price { font-size: 12px; font-weight: 600; color: #334155; }
.detail-pos-chg { font-size: 11px; font-weight: 600; }
.detail-main { flex: 1; min-width: 0; }
.m-pos-strip-wrap { position: sticky; top: 44px; z-index: 15; margin: -8px -8px 8px; padding: 6px 8px; background: #f2f4f8; border-bottom: 1px solid #e5e9f0; }
.m-pos-strip { display: flex; gap: 6px; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
.m-pos-strip--two-rows { display: grid; grid-auto-flow: column; grid-template-rows: repeat(2, auto); grid-auto-columns: minmax(112px, 138px); gap: 5px 6px; }
.m-pos-strip::-webkit-scrollbar { display: none; }
.m-pos-chip { flex: 0 0 auto; min-width: 112px; max-width: 148px; padding: 4px 7px; border: 1px solid #e2e8f0; border-radius: 7px; background: #fff; text-align: left; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
.m-pos-chip.is-active { border-color: #2563eb; background: #eff6ff; }
.m-pos-chip-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.m-pos-chip-name { font-size: 12px; color: #182230; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.m-pos-chip-chg { font-size: 11px; font-weight: 600; flex-shrink: 0; white-space: nowrap; }
.m-pos-chip-sub { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 10px; color: #7d899b; white-space: nowrap; }
</style>
