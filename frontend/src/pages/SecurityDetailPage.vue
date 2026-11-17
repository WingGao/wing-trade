<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Account, Currency, IntradayRead, Lot, LotsRead, PositionRefreshScope, Quote, Security, SettledTradeDetail } from '../api/types'
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
import { formatDateTime, formatMoney } from '../utils/format'
import { isPositionRefreshing, refreshPosition } from '../utils/recalculate'

const route = useRoute()
const preferences = usePreferencesStore()
const ledger = useLedgerStore()
const security = ref<Security | null>(null)
const accounts = ref<Account[]>([])
const accountId = ref('')
const lots = ref<Lot[]>([])
const closedTradeDetails = ref<SettledTradeDetail[]>([])
const closedLotCount = ref(0)
const realizedPnl = ref('0')
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
const selectedLotId = ref('')
const selectedLot = computed(() => lots.value.find((lot) => lot.id === selectedLotId.value))
const currency = computed<Currency>(() => security.value?.currency ?? (security.value?.market === 'US' ? 'USD' : 'CNY'))
const matchingAccounts = computed(() => accounts.value.filter((account) => account.currency === currency.value))
const requestedStrategyId = computed(() => typeof route.query.strategyId === 'string' ? route.query.strategyId : undefined)
const refreshScope = computed<PositionRefreshScope | null>(() => security.value && accountId.value ? { accountId: accountId.value, securityId: security.value.id, strategyId: resolvedStrategyId.value ?? requestedStrategyId.value, market: security.value.market, symbol: security.value.symbol, name: security.value.name } : null)
const rebuilding = computed(() => refreshScope.value ? isPositionRefreshing(refreshScope.value) : false)
const currentQuote = computed(() => {
  const quote = lotQuote.value ?? intraday.value?.quote
  return quote && (marketError.value || intraday.value?.isStale) ? { ...quote, isStale: true } : quote
})
const hasOpenLots = computed(() => lots.value.some((lot) => lot.status === 'OPEN'))
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
  closedTradeDetails.value = preferences.includeClosed ? result.closedTradeDetails ?? [] : []
  closedLotCount.value = result.closedLotCount
  realizedPnl.value = result.realizedPnl
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
  if (!currentSecurity || !account) { lots.value = []; closedTradeDetails.value = []; needsRefresh.value = false; return false }
  const result = await api.lots(currentSecurity.id, includeClosed, account, strategy)
  if (!alive || currentGeneration !== generation || currentDataGeneration !== dataGeneration || request !== lotGeneration || account !== accountId.value || strategy !== requestedStrategyId.value || includeClosed !== preferences.includeClosed) return false
  if ((result.accountId && result.accountId !== account) || (result.strategyId && strategy && result.strategyId !== strategy)) throw new Error('返回的持仓范围与当前账户、策略不一致')
  applyLots(result)
  return true
}

/** 每次行情请求完成三秒后再刷新，保证同一页面代次只有一个请求在途。 */
function schedulePoll() {
  clearTimer()
  if (!alive || document.hidden || !security.value || mutationActive.value) return
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
      needsRefresh.value = false
    }
    if (!mutationActive.value) void reloadLots()
  }
})
onMounted(() => { alive = true; document.addEventListener('visibilitychange', handleVisibility); void loadPage() })
onBeforeUnmount(() => { alive = false; generation += 1; lotGeneration += 1; clearTimer(); document.removeEventListener('visibilitychange', handleVisibility) })
</script>

<template>
  <section class="page-head detail-head">
    <div><h2>{{ route.params.market }}:{{ route.params.symbol }} {{ security?.name ?? '' }}</h2><p class="muted">批次、配对和止盈设置</p></div>
    <div class="toolbar"><el-select v-if="matchingAccounts.length" v-model="accountId" aria-label="当前账户" style="width:170px"><el-option v-for="account in matchingAccounts" :key="account.id" :value="account.id" :label="account.name" /></el-select><el-button @click="globalTargetVisible = true">全局止盈设置</el-button><el-button :disabled="!security" @click="securityTargetVisible = true">个股止盈设置</el-button><el-button :disabled="!security || !accountId" @click="buyVisible = true">录入买入</el-button><el-button type="danger" :disabled="!security || !accountId || !hasOpenLots" @click="sellVisible = true">卖出配对</el-button></div>
  </section>
  <el-alert v-if="pageError" :title="pageError" type="error" :closable="false" class="status-alert" />
  <el-alert v-if="security && !accountId" :title="`暂无 ${currency} 账户，请先在总览中创建同币种账户后记账。`" type="warning" :closable="false" class="status-alert" />
  <el-card class="panel">
    <template #header><div class="panel-title chart-heading"><span>当前日股价图 <span class="muted">{{ intraday?.tradingDate ?? '' }} · 每 3 秒刷新</span></span><QuoteBadge :quote="currentQuote" :currency="currency" /></div></template>
    <el-alert v-if="marketError" :title="`行情刷新失败：${marketError}；保留最后有效数据作为过期参考。`" type="warning" :closable="false" class="status-alert" />
    <div class="muted">来源 {{ intraday?.provider ?? '—' }} · 获取 {{ formatDateTime(intraday?.fetchedAt) }}<span v-if="intraday?.isStale || marketError"> · 行情已过期</span></div>
    <IntradayChart :data="intraday" />
  </el-card>
  <el-card class="panel">
    <template #header><div class="panel-title lot-heading"><span>持仓批次 <span class="muted">已结清 {{ closedLotCount }} 批 · 累计已实现收益 {{ formatMoney(realizedPnl, currency) }}</span></span><div class="toolbar"><el-switch v-model="preferences.includeClosed" active-text="显示已结清" /><el-button :disabled="!security || !accountId" :loading="rebuilding" @click="recalculate">重新计算</el-button></div></div></template>
    <el-alert v-if="needsRefresh" type="warning" :closable="false" show-icon class="status-alert" title="需要刷新：交易流水已更正，当前持仓与收益尚未重新核算。">
      <p>刷新仅重新计算当前账户、个股与策略；已有显式止盈目标请在重算后核对。</p>
      <el-button type="warning" :loading="rebuilding" @click="recalculate">刷新持仓</el-button>
    </el-alert>
    <div v-loading="loading || rebuilding"><LotTable :lots="lots" :include-closed="preferences.includeClosed" :currency="currency" @set-target="addTarget" /></div>
    <section v-if="preferences.includeClosed" class="settled-trade-section">
      <h3 class="panel-title">已结清买卖明细</h3>
      <p class="muted">显示已结清批次的原始买入和全部有效卖出；跨批次卖出仅展示本批数量、分摊净回款和已实现盈亏。</p>
      <SettledTradeTable :details="closedTradeDetails" :currency="currency" />
    </section>
  </el-card>
  <BuyTradeDialog v-if="security" v-model="buyVisible" :security-id="security.id" :account-id="accountId" :currency="currency" @saved="reloadLots" />
  <SellMatchDrawer v-if="security" v-model="sellVisible" :security-id="security.id" :account-id="accountId" :currency="currency" :lots="lots" @saved="reloadLots" />
  <ProfitTargetDialog v-if="selectedLot" v-model="targetVisible" :lot-id="selectedLot.id" :lot="selectedLot" @saved="reloadLots" />
  <ProfitSettingDialog v-model="globalTargetVisible" @saved="reloadLots" />
  <ProfitSettingDialog v-if="security" v-model="securityTargetVisible" :security-id="security.id" @saved="reloadLots" />
</template>

<style scoped>
.detail-head { gap: 16px; flex-wrap: wrap; }
.detail-head .toolbar { flex-wrap: wrap; }
.chart-heading, .lot-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.settled-trade-section { margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--el-border-color-lighter); }
.settled-trade-section h3 { margin: 0 0 8px; font-size: 16px; }
</style>
