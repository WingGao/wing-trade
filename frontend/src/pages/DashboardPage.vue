<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { Currency, DashboardMode, DashboardPosition, Lot, PositionRefreshScope, Security, SecurityCandidate, ValuationStatus } from '../api/types'
import { useAccountsStore } from '../stores/accounts'
import { useLedgerStore } from '../stores/ledger'
import { usePreferencesStore } from '../stores/preferences'
import { useQuotesStore } from '../stores/quotes'
import {
  mergePositionsWithWatchlist,
  projectWatchlistWithQuotes,
  useWatchlistStore,
} from '../stores/watchlist'
import { api } from '../api/client'
import ActivePositionList from '../components/ActivePositionList.vue'
import SecurityDetailPanel from '../components/SecurityDetailPanel.vue'
import QuoteBadge from '../components/QuoteBadge.vue'
import BuyTradeDialog from '../components/BuyTradeDialog.vue'
import SellMatchDrawer from '../components/SellMatchDrawer.vue'
import ProfitSettingDialog from '../components/ProfitSettingDialog.vue'
import NotificationSettingDialog from '../components/NotificationSettingDialog.vue'
import { formatDateTime, formatMoney, formatPercent, isFundSecurity, marketSessionLabel, trendClass, valuationStatusLabel } from '../utils/format'
import { refreshPosition } from '../utils/recalculate'
import { projectDashboardWithQuotes } from '../utils/valuation'
import { useViewport } from '../utils/viewport'

const accountsStore = useAccountsStore()
const ledger = useLedgerStore()
const preferences = usePreferencesStore()
const quotesStore = useQuotesStore()
const watchlistStore = useWatchlistStore()
const router = useRouter()
const { isMobile } = useViewport()
const dashboard = computed(() =>
  ledger.dashboard ? projectDashboardWithQuotes(ledger.dashboard, quotesStore.quotesMap) : null,
)
const projectedWatchlist = computed(() =>
  projectWatchlistWithQuotes(watchlistStore.items, quotesStore.quotesMap),
)
const positions = computed(() =>
  mergePositionsWithWatchlist(
    dashboard.value?.positions ?? [],
    projectedWatchlist.value,
    preferences.showWatchlist,
  ),
)
const selectedPositionKey = ref<string | null>(null)
const streamStatusText = computed(() => {
  if (quotesStore.connectionState === 'live') return '实时推送中'
  if (quotesStore.connectionState === 'reconnecting') return '行情重连中'
  return ''
})
const dashboardCurrency = computed<Currency | null>(() => dashboard.value?.currency ?? accountsStore.activeAccount?.currency ?? null)
const marketSessionText = computed(() => {
  const phase = dashboard.value?.marketSession
  if (!phase) return ''
  const prefix = dashboardCurrency.value === 'USD' ? '美股' : 'A股'
  return `${prefix}${marketSessionLabel(phase)}`
})
const securityKeyword = ref('')
const securityResults = ref<SecurityCandidate[]>([])
const searching = ref(false)
const searchError = ref('')
const searched = ref(false)
const globalTargetVisible = ref(false)
const notificationSettingVisible = ref(false)
const selectedSecurity = ref<Security | null>(null)
const selectedLots = ref<Lot[]>([])
const accounts = computed(() => accountsStore.accounts)
const selectedAccountId = ref('')
const marketChoice = ref<'CN' | 'US' | 'OF'>('CN')
const securityName = ref('')
const buyVisible = ref(false)
const sellVisible = ref(false)
const pageAlive = ref(false)
const manualRefreshing = ref(false)
const activeMutations = ref(0)
const mutationActive = computed(() => activeMutations.value > 0)

/** 合并服务端待刷新清单与行状态，旧响应仍可按行提供入口。 */
const pendingScopes = computed(() => {
  const scopes = new Map<string, PositionRefreshScope>()
  for (const scope of dashboard.value?.pendingRefreshScopes ?? []) scopes.set(`${scope.securityId}|${scope.strategyId ?? ''}`, scope)
  for (const position of positions.value) {
    if (position.needsRefresh && dashboard.value) {
      const scope = { ...position, accountId: dashboard.value.accountId }
      scopes.set(`${scope.securityId}|${scope.strategyId}`, scope)
    }
  }
  return [...scopes.values()]
})
const unlistedScopes = computed(() =>
  pendingScopes.value.filter(
    (scope) => !positions.value.some((position) => position.securityId === scope.securityId && position.strategyId === scope.strategyId),
  ),
)
const selectedAccount = computed(() => accounts.value.find((account) => account.id === selectedAccountId.value))
const accountMatchesSecurity = computed(() => !selectedSecurity.value || selectedAccount.value?.currency === requiredCurrency.value)
const requiredCurrency = computed<'CNY' | 'USD'>(() => selectedSecurity.value?.currency ?? (selectedSecurity.value?.market === 'US' ? 'USD' : 'CNY'))

/** 构造持仓行的稳定唯一键。 */
function positionKey(position: DashboardPosition): string {
  return `${position.securityId}|${position.strategyId}`
}

/** 按默认当日涨幅降序挑出首个可展示详情的持仓（缺行情置后）。 */
function pickDefaultDetailPosition(list: DashboardPosition[]): DashboardPosition | null {
  const candidates = [...list]
    .filter((item) => Boolean(item.market && item.symbol))
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
  return candidates[0] ?? null
}

/** 当前 PC 右栏联动展示的个股持仓对象。 */
const activeDetailPosition = computed<DashboardPosition | null>(() => {
  if (!positions.value.length) return null
  if (selectedPositionKey.value) {
    const matched = positions.value.find((item) => positionKey(item) === selectedPositionKey.value && item.market && item.symbol)
    if (matched) return matched
  }
  return pickDefaultDetailPosition(positions.value)
})

const effectiveSelectedKey = computed<string | null>(() =>
  activeDetailPosition.value ? positionKey(activeDetailPosition.value) : null,
)

/** 在 PC 端点击左侧活动仓位时，切换右侧详情面板展示的目标持仓。 */
function selectDesktopPosition(position: DashboardPosition): void {
  if (!position.market || !position.symbol) {
    void openPosition(position)
    return
  }
  selectedPositionKey.value = positionKey(position)
}

let pollTimer: number | null = null
let responseGeneration = 0
let queryGeneration = 0
let searchGeneration = 0

/** 请求失败时保留上一份一致快照，但将最新状态降级为过期参考估值。 */
const valuationStatus = computed<ValuationStatus | null>(() => {
  const status = dashboard.value?.summary.valuationStatus ?? null
  if (!status) return null
  return ledger.error && status === 'fresh' ? 'stale' : status
})

/** 生成账户级估值覆盖说明，避免把部分行情当作完整账户收益。 */
const valuationMessage = computed(() => {
  const current = dashboard.value
  if (!current) return '正在加载账务与缓存估值'
  const summary = current.summary
  if (ledger.error) return `估值刷新失败，已保留 ${formatDateTime(current.asOf)} 的一致快照并标记为过期。`
  switch (summary.valuationStatus) {
    case 'fresh':
      return `行情覆盖完整：${summary.quotedOpenPositions}/${summary.totalOpenPositions} 个未结清持仓为有效行情，估值时间 ${formatDateTime(current.asOf)}。`
    case 'stale':
      return `全部持仓有行情，其中 ${summary.staleOpenPositions} 项已过期；以下为明确标注的参考估值。`
    case 'partial':
      return `${summary.totalOpenPositions} 个未结清持仓中仅 ${summary.quotedOpenPositions} 个有行情，账户未实现收益不可用。`
    case 'unavailable':
      return '行情不可用，账户未实现收益不可用；交易记录和快速记账仍可正常使用。'
    case 'empty':
      return '当前账户没有未结清持仓，未实现收益为零、收益率为空。'
  }
})

/** 将估值状态映射为 Element Plus 的标签颜色。 */
function statusTagType(status: ValuationStatus | null): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'fresh' || status === 'empty') return 'success'
  if (status === 'stale') return 'warning'
  if (status === 'partial' || status === 'unavailable') return 'danger'
  return 'info'
}

/** 将估值状态映射为提示条类型。 */
function statusAlertType(status: ValuationStatus | null): 'success' | 'warning' | 'error' | 'info' {
  if (status === 'fresh' || status === 'empty') return 'success'
  if (status === 'stale') return 'warning'
  if (status === 'partial' || status === 'unavailable') return 'error'
  return 'info'
}

/** 返回账户未实现收益展示值，覆盖不完整时禁止显示局部小计。 */
const unrealizedValue = computed(() => {
  if (!dashboard.value) return '—'
  if (valuationStatus.value === 'partial' || valuationStatus.value === 'unavailable') return '账户浮盈不可用'
  if (valuationStatus.value === 'empty') return formatMoney('0', dashboardCurrency.value)
  return formatMoney(dashboard.value.summary.unrealizedPnl, dashboardCurrency.value)
})

/** 市值与浮盈复用同一估值状态，行情不完整时禁止显示部分总值。 */
const marketValue = computed(() => {
  if (!dashboard.value) return '—'
  if (valuationStatus.value === 'partial' || valuationStatus.value === 'unavailable') return '持仓总价值不可用'
  if (valuationStatus.value === 'empty') return formatMoney('0', dashboardCurrency.value)
  return formatMoney(dashboard.value.summary.marketValue, dashboardCurrency.value)
})

/** 返回账户未实现收益率展示值，覆盖不完整或空仓时不伪造数值。 */
const unrealizedRate = computed(() => {
  if (valuationStatus.value === 'partial' || valuationStatus.value === 'unavailable') return '收益率不可用'
  if (valuationStatus.value === 'empty') return '收益率 —'
  return `收益率 ${formatPercent(dashboard.value?.summary.unrealizedReturnPct)}`
})

/** 加载账户列表并同步全局当前账户到快速记账选择器。 */
async function loadAccounts() {
  try {
    const result = await accountsStore.loadAccounts()
    if (!selectedAccountId.value) selectedAccountId.value = accountsStore.activeAccountId || result[0]?.id || ''
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '加载账户失败')
  }
}

/** 清理自动轮询计时器，确保隐藏或重载时不会叠加任务。 */
function clearPollTimer() {
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = null
}

/** 按服务器返回的间隔安排下一次自动估值；SSE 处于实时推送态时停止定时轮询。 */
function schedulePoll(seconds: number | undefined) {
  clearPollTimer()
  if (!pageAlive.value || document.hidden || mutationActive.value || quotesStore.isLive) return
  const parsed = Number(seconds)
  const delaySeconds = Number.isFinite(parsed) ? Math.max(1, parsed) : 60
  pollTimer = window.setTimeout(() => {
    pollTimer = null
    void loadValuation('auto')
  }, delaySeconds * 1000)
}

/** 通过同一首页接口加载整份估值，并用代次防止旧响应覆盖新选择。 */
async function loadValuation(mode: DashboardMode, reportSuccess = false, scheduleNext = true, afterWrite = false) {
  if (mutationActive.value && !afterWrite) return null
  const requestId = ++responseGeneration
  clearPollTimer()
  const targetAccountId = accountsStore.activeAccountId || dashboard.value?.accountId
  const [result] = await Promise.all([
    ledger.loadDashboard({
      accountId: targetAccountId,
      includeClosed: preferences.includeClosed,
      mode,
      reuse: mode === 'auto' && !afterWrite,
    }),
    watchlistStore.load(targetAccountId),
  ])
  if (!pageAlive.value || requestId !== responseGeneration) return null
  if (scheduleNext) schedulePoll(result?.pollAfterSeconds)
  if (reportSuccess && result) ElMessage.success(mode === 'manual' ? '估值与行情刷新完成' : '首页数据已更新')
  return result
}

/** 页面隐藏时暂停自动刷新，恢复可见后立即读取一次最新估值。 */
function handleVisibilityChange() {
  clearPollTimer()
  if (!document.hidden && pageAlive.value) void loadValuation('auto')
}

/** 手动刷新始终请求行情，但保留与自动刷新的串行约束。 */
async function refresh() {
  if (manualRefreshing.value) return
  manualRefreshing.value = true
  try {
    await loadValuation('manual', true)
  } finally {
    manualRefreshing.value = false
  }
}

/** 同时查询外部名称与本地目录；外部不可用时保留可用的本地结果。 */
async function searchSecurities() {
  const keyword = securityKeyword.value.trim()
  const generation = ++searchGeneration
  if (!keyword) {
    securityResults.value = []
    return
  }
  const symbol = keyword.split(/[:\s]/).filter(Boolean).at(-1) ?? keyword
  marketChoice.value = /^[A-Za-z]+$/.test(symbol) ? 'US' : 'CN'
  searching.value = true
  searchError.value = ''
  searched.value = true
  selectedSecurity.value = null
  selectedLots.value = []
  securityResults.value = []
  try {
    const [local, external] = await Promise.allSettled([api.securities(keyword), api.lookupSecurities(keyword)])
    if (generation !== searchGeneration || keyword !== securityKeyword.value.trim()) return
    const merged = new Map<string, SecurityCandidate>()
    if (local.status === 'fulfilled') local.value.forEach((item) => merged.set(`${item.market}:${item.symbol}`, item))
    if (external.status === 'fulfilled') {
      external.value.securities.forEach((item) => {
        const key = `${item.market}:${item.symbol}`
        merged.set(key, { ...merged.get(key), ...item, id: item.id ?? merged.get(key)?.id })
      })
      searchError.value = external.value.error ?? ''
    } else searchError.value = `外部名称查询失败：${external.reason instanceof Error ? external.reason.message : '暂不可用'}；可继续使用本地结果或手动新增。`
    if (local.status === 'rejected' && external.status === 'rejected') searchError.value = '证券查询失败，请稍后重试或手动填写证券信息。'
    securityResults.value = [...merged.values()]
  } finally {
    if (generation === searchGeneration) searching.value = false
  }
}

/** 创建本地证券并立即选中，便于继续快速记账。 */
async function createSecurity() {
  const raw = securityKeyword.value.trim().split(/\s/)[0].toUpperCase()
  const symbol = raw.includes(':') ? raw.split(':')[1] : raw
  if (!symbol || !securityName.value.trim()) { ElMessage.warning('请填写证券代码和名称'); return }
  try {
    const market = raw.includes(':') ? raw.split(':')[0] : marketChoice.value === 'US' ? 'US' : marketChoice.value === 'OF' ? 'OF' : /^(000|001|002|003|300|301)\d{3}$/.test(symbol) ? 'SZ' : /^(4|8|92)/.test(symbol) ? 'BJ' : 'SH'
    const securityType = market === 'OF' ? 'FUND' : 'STOCK'
    const security = await api.createSecurity({ market, symbol, name: securityName.value.trim(), securityType })
    await chooseSecurity(security)
    ElMessage.success(`已添加 ${security.market}:${security.symbol}`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '新增证券失败')
  }
}

/** 选中证券并读取对应账户下的未结清批次。 */
async function chooseSecurity(candidate: SecurityCandidate) {
  let security: Security
  try {
    security = candidate.id ? { ...candidate, id: candidate.id } : await api.createSecurity({ market: candidate.market, symbol: candidate.symbol, name: candidate.name, securityType: candidate.securityType ?? (candidate.market === 'OF' ? 'FUND' : 'STOCK') })
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '添加证券失败'); return }
  selectedSecurity.value = security
  securityKeyword.value = `${security.market}:${security.symbol} ${security.name ?? ''}`.trim()
  securityResults.value = []
  selectedAccountId.value = accounts.value.find((item) => item.currency === (security.currency ?? (security.market === 'US' ? 'USD' : 'CNY')))?.id ?? selectedAccountId.value
  try {
    selectedLots.value = (await api.lots(security.id, false, selectedAccountId.value)).lots
  } catch {
    selectedLots.value = []
  }
}

/** 创建当前证券币种对应的账户，并同步切换全局账户与刷新批次列表。 */
async function createAccountForCurrency(currency: 'CNY' | 'USD') {
  try {
    const result = await ElMessageBox.prompt(`请输入 ${currency} 账户名称`, `创建 ${currency} 账户`, { inputValue: currency === 'USD' ? '美股账户' : 'A股账户' })
    const account = await accountsStore.createAccount({ name: result.value, currency })
    selectedAccountId.value = account.id
    ElMessage.success(`${currency} 账户已创建`)
    if (selectedSecurity.value) selectedLots.value = (await api.lots(selectedSecurity.value.id, false, account.id)).lots
  } catch {
    // 用户取消或接口失败时不改变当前记录。
  }
}

/** 记账或右侧详情变更成功后重载整份估值，同时保留“显示已结清”和当前批次选择。 */
async function reloadQuickTrade() {
  ledger.invalidateDashboard()
  await loadValuation('auto', false, true, true)
  if (selectedSecurity.value) {
    selectedLots.value = (await api.lots(selectedSecurity.value.id, false, selectedAccountId.value)).lots
  }
}

/** 根据持仓内置证券标识打开独立个股详情页，不再依赖独立行情状态。 */
async function openPosition(position: DashboardPosition) {
  const account = dashboard.value?.accountId
  const realStrategyId = position.strategyId?.startsWith('watchlist:') ? undefined : position.strategyId
  if (position.market && position.symbol) {
    router.push({ path: `/security/${position.market}/${position.symbol}`, query: { accountId: account, strategyId: realStrategyId } })
    return
  }
  const securities = await api.securities()
  const security = securities.find((item) => item.id === position.securityId)
  if (security && pageAlive.value && account === dashboard.value?.accountId) router.push({ path: `/security/${security.market}/${security.symbol}`, query: { accountId: account, strategyId: realStrategyId } })
  else ElMessage.warning('找不到证券基础信息')
}

/** 在刷新前后失效旧读取，统一处理确认、防重和服务端状态重读。 */
async function recalculate(scope: PositionRefreshScope) {
  if (!scope.accountId) return
  const account = dashboard.value?.accountId
  let started = false
  await refreshPosition(scope, {
    isCurrent: () => pageAlive.value && account === dashboard.value?.accountId,
    invalidate: () => {
      if (!started) { started = true; activeMutations.value += 1 }
      responseGeneration += 1
      clearPollTimer()
      ledger.invalidateDashboard()
    },
    reload: async () => {
      while (pageAlive.value && account === dashboard.value?.accountId) {
        const query = queryGeneration
        const result = await loadValuation('auto', false, false, true)
        if (query === queryGeneration) return Boolean(result)
      }
      return false
    },
    settled: () => { activeMutations.value -= 1; schedulePoll(dashboard.value?.pollAfterSeconds) },
  })
}

/** 首次进入先加载账户并显示缓存，再请求自动估值。 */
async function initializePage() {
  await loadAccounts()
  await loadValuation('cache', false, false)
  if (!document.hidden) await loadValuation('auto')
}

watch(() => accountsStore.activeAccountId, (nextAccountId) => {
  if (!nextAccountId) return
  if (!selectedSecurity.value) selectedAccountId.value = nextAccountId
  if (!pageAlive.value || nextAccountId === dashboard.value?.accountId) return
  selectedPositionKey.value = null
  queryGeneration += 1
  ledger.invalidateDashboard()
  void loadValuation('auto')
})

watch(() => quotesStore.connectionState, (state) => {
  if (!pageAlive.value) return
  if (state === 'live') {
    clearPollTimer()
  } else if (state === 'offline') {
    schedulePoll(dashboard.value?.pollAfterSeconds)
  }
})

watch(() => preferences.includeClosed, () => {
  queryGeneration += 1
  if (!pageAlive.value) return
  if (mutationActive.value) {
    responseGeneration += 1
    ledger.invalidateDashboard()
    return
  }
  void loadValuation('auto')
})

onMounted(() => {
  pageAlive.value = true
  document.addEventListener('visibilitychange', handleVisibilityChange)
  void initializePage()
})

onBeforeUnmount(() => {
  pageAlive.value = false
  responseGeneration += 1
  ledger.invalidateDashboard()
  clearPollTimer()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <template v-if="!isMobile">
    <section class="page-head">
      <div><h2>账户总览</h2><p class="muted">以真实交易流水为准，价格、浮盈和汇总来自同一次估值响应。</p></div>
      <div class="toolbar">
        <el-tag v-if="streamStatusText" :type="quotesStore.isLive ? 'success' : 'warning'" effect="plain">{{ streamStatusText }}</el-tag>
        <el-tag v-if="marketSessionText" :type="dashboard?.marketSessionOpen ? 'success' : 'info'" effect="plain">{{ marketSessionText }}</el-tag>
        <el-tag :type="statusTagType(valuationStatus)">{{ valuationStatusLabel(valuationStatus) }}</el-tag>
        <el-button @click="globalTargetVisible = true">全局止盈设置</el-button>
        <el-button @click="notificationSettingVisible = true">推送设置</el-button>
        <el-switch v-model="preferences.showWatchlist" active-text="显示关注" />
        <el-switch v-model="preferences.includeClosed" active-text="显示已结清" />
        <el-button type="primary" :loading="manualRefreshing" :disabled="mutationActive" @click="refresh">刷新行情</el-button>
      </div>
    </section>

    <el-alert v-if="ledger.error" :title="`估值刷新失败：${ledger.error}。账务记录仍可正常使用。`" type="error" show-icon :closable="false" class="status-alert" />
    <el-alert v-if="dashboard" :title="valuationMessage" :type="statusAlertType(valuationStatus)" show-icon :closable="false" class="status-alert" />

    <el-card class="panel quick-trade">
      <template #header><div class="panel-title">快速记账</div></template>
      <div class="search-row">
        <el-input v-model="securityKeyword" placeholder="输入证券代码、名称，例如 600519" clearable @keyup.enter="searchSecurities" />
        <el-button type="primary" :loading="searching" @click="searchSecurities">查找证券</el-button>
        <el-select v-if="accounts.length" v-model="selectedAccountId" style="width:190px" placeholder="选择账户"><el-option v-for="account in accounts" :key="account.id" :label="`${account.name} (${account.currency})`" :value="account.id" /></el-select>
        <el-button v-if="selectedSecurity" :disabled="!accountMatchesSecurity" @click="buyVisible = true">买入记账</el-button>
        <el-button v-if="selectedSecurity" type="danger" :disabled="!accountMatchesSecurity || !selectedLots.length" @click="sellVisible = true">卖出配对</el-button>
        <el-button v-if="selectedSecurity && !accounts.some((item) => item.currency === requiredCurrency)" @click="createAccountForCurrency(requiredCurrency)">创建 {{ requiredCurrency }} 账户</el-button>
      </div>
      <div v-if="securityResults.length" class="security-results">
        <button v-for="security in securityResults" :key="`${security.market}:${security.symbol}`" class="security-option" type="button" @click="chooseSecurity(security)"><strong>{{ security.market }}:{{ security.symbol }}</strong><span>{{ security.name ?? '名称不可用' }}</span></button>
      </div>
      <el-alert v-if="searchError" :title="searchError" type="warning" :closable="false" class="status-alert" />
      <div v-if="searched && !searching && securityKeyword && !securityResults.length && !selectedSecurity" class="security-create">
        <span class="muted">本地证券库暂无匹配项，可先新增：</span>
        <el-select v-model="marketChoice" style="width:108px"><el-option label="A股" value="CN" /><el-option label="场外基金" value="OF" /><el-option label="美股" value="US" /></el-select>
        <el-input v-model="securityName" placeholder="证券名称" />
        <el-button type="success" @click="createSecurity">新增证券</el-button>
      </div>
      <div v-if="selectedSecurity" class="selected-security">
        当前证券：<strong>{{ selectedSecurity.market }}:{{ selectedSecurity.symbol }}</strong> {{ selectedSecurity.name ?? '' }}
        <span v-if="!accountMatchesSecurity" class="currency-warning">当前账户为 {{ selectedAccount?.currency ?? '未选择' }}，请选择 {{ requiredCurrency }} 账户</span>
        <span v-else-if="!selectedLots.length" class="muted">暂无未结清批次，可直接录入买入</span>
      </div>
    </el-card>

    <div class="metric-grid dashboard-metrics">
      <el-card class="metric-card"><span class="metric-label">持仓总价值</span><div class="metric-value">{{ marketValue }}</div><div class="metric-detail">{{ valuationStatus === 'stale' ? '参考估值 · 行情已过期' : '按当前持仓与同次行情估值' }}</div></el-card>
      <el-card class="metric-card"><span class="metric-label">累计买入现金流</span><div class="metric-value">{{ formatMoney(dashboard?.summary.totalBuyCashOut, dashboardCurrency) }}</div></el-card>
      <el-card class="metric-card"><span class="metric-label">累计卖出回款</span><div class="metric-value">{{ formatMoney(dashboard?.summary.totalSellCashIn, dashboardCurrency) }}</div></el-card>
      <el-card class="metric-card"><span class="metric-label">已实现收益</span><div :class="['metric-value', trendClass(dashboard?.summary.realizedPnl)]">{{ formatMoney(dashboard?.summary.realizedPnl, dashboardCurrency) }}</div></el-card>
      <el-card class="metric-card">
        <span class="metric-label">账户未实现浮盈（参考）</span>
        <div :class="['metric-value', trendClass(dashboard?.summary.unrealizedPnl)]">{{ unrealizedValue }}</div>
        <div class="metric-detail">{{ unrealizedRate }} · 未扣预计卖出费用</div>
      </el-card>
    </div>

    <div class="dashboard-workspace">
      <div class="workspace-positions">
        <ActivePositionList
          :positions="positions"
          :total-open-positions="dashboard?.summary.totalOpenPositions ?? 0"
          :currency="dashboardCurrency"
          :account-id="dashboard?.accountId ?? ''"
          :loading="ledger.loading"
          :active-key="effectiveSelectedKey"
          :pending-scopes="pendingScopes"
          :unlisted-scopes="unlistedScopes"
          @select="selectDesktopPosition"
          @recalculate="recalculate"
        />
      </div>
      <div class="workspace-detail">
        <SecurityDetailPanel
          v-if="activeDetailPosition?.market && activeDetailPosition?.symbol"
          :market="activeDetailPosition.market"
          :symbol="activeDetailPosition.symbol"
          :account-id="dashboard?.accountId"
          :strategy-id="activeDetailPosition.strategyId?.startsWith('watchlist:') ? undefined : activeDetailPosition.strategyId"
          embedded
          @mutated="reloadQuickTrade"
          @open-standalone="openPosition(activeDetailPosition)"
        />
        <el-card v-else class="panel workspace-empty-card">
          <div class="empty">暂无活动持仓，可在上方快速记账中录入买入后查看个股详情</div>
        </el-card>
      </div>
    </div>

    <el-card class="panel">
      <template #header><div class="panel-title">自选股行情</div></template>
      <div class="quote-grid">
        <div v-for="quote in dashboard?.quotes ?? []" :key="quote.symbol" class="quote-card">
          <div class="quote-symbol">{{ quote.symbol }}</div>
          <QuoteBadge :quote="quote" :currency="quote.currency ?? dashboardCurrency" />
        </div>
        <div v-if="!dashboard?.quotes.length" class="empty">暂无自选股行情</div>
      </div>
    </el-card>
  </template>

  <template v-else>
    <section class="m-section m-dash-head">
      <div class="m-dash-toolbar">
        <el-tag :type="statusTagType(valuationStatus)" size="small">{{ valuationStatusLabel(valuationStatus) }}</el-tag>
        <div class="m-dash-actions">
          <el-switch v-model="preferences.showWatchlist" size="small" active-text="关注" />
          <el-switch v-model="preferences.includeClosed" size="small" active-text="已结清" />
          <el-button size="small" @click="globalTargetVisible = true">全局止盈</el-button>
          <el-button size="small" @click="notificationSettingVisible = true">推送</el-button>
          <el-button size="small" type="primary" :loading="manualRefreshing" :disabled="mutationActive" @click="refresh">刷新</el-button>
        </div>
      </div>
      <div v-if="ledger.error" class="m-compact-note is-error">估值刷新失败：{{ ledger.error }}。账务记录仍可正常使用。</div>
      <div v-if="dashboard" :class="['m-compact-note', `is-${statusAlertType(valuationStatus)}`]">{{ valuationMessage }}</div>
    </section>

    <section class="m-section m-summary-grid" aria-label="账户汇总">
      <div class="m-summary-top">
        <div class="m-summary-cell">
          <span class="m-summary-label">持仓总价值</span>
          <strong class="m-summary-main num">{{ marketValue }}</strong>
          <span class="m-summary-sub">{{ valuationStatus === 'stale' ? '参考估值 · 行情已过期' : '按当前持仓与同次行情' }}</span>
        </div>
        <div class="m-summary-cell">
          <span class="m-summary-label">账户未实现浮盈</span>
          <strong :class="['m-summary-main', 'num', trendClass(dashboard?.summary.unrealizedPnl)]">{{ unrealizedValue }}</strong>
          <span class="m-summary-sub num">{{ unrealizedRate }}</span>
        </div>
      </div>
      <div class="m-summary-bottom">
        <div class="m-summary-mini">
          <span class="m-summary-label">累计买入</span>
          <strong class="m-summary-mini-val num">{{ formatMoney(dashboard?.summary.totalBuyCashOut, dashboardCurrency) }}</strong>
        </div>
        <div class="m-summary-mini">
          <span class="m-summary-label">累计卖出</span>
          <strong class="m-summary-mini-val num">{{ formatMoney(dashboard?.summary.totalSellCashIn, dashboardCurrency) }}</strong>
        </div>
        <div class="m-summary-mini">
          <span class="m-summary-label">已实现收益</span>
          <strong :class="['m-summary-mini-val', 'num', trendClass(dashboard?.summary.realizedPnl)]">{{ formatMoney(dashboard?.summary.realizedPnl, dashboardCurrency) }}</strong>
        </div>
      </div>
    </section>

    <section class="m-section m-quick-trade">
      <div class="m-quick-row">
        <el-input v-model="securityKeyword" size="small" placeholder="输入证券代码/名称，如 600519" clearable @keyup.enter="searchSecurities" />
        <el-button size="small" type="primary" :loading="searching" @click="searchSecurities">查找</el-button>
      </div>
      <div v-if="securityResults.length" class="m-security-results">
        <button v-for="security in securityResults" :key="`${security.market}:${security.symbol}`" class="m-security-option" type="button" @click="chooseSecurity(security)">
          <strong>{{ security.market }}:{{ security.symbol }}</strong>
          <span>{{ security.name ?? '名称不可用' }}</span>
        </button>
      </div>
      <div v-if="searchError" class="m-compact-note is-warning">{{ searchError }}</div>
      <div v-if="searched && !searching && securityKeyword && !securityResults.length && !selectedSecurity" class="m-security-create">
        <span class="m-section-sub">无匹配项，可新增：</span>
        <div class="m-security-create-row">
          <el-select v-model="marketChoice" size="small" style="width:94px"><el-option label="A股" value="CN" /><el-option label="场外基金" value="OF" /><el-option label="美股" value="US" /></el-select>
          <el-input v-model="securityName" size="small" placeholder="证券名称" />
          <el-button size="small" type="success" @click="createSecurity">新增</el-button>
        </div>
      </div>
      <div v-if="selectedSecurity" class="m-selected-box">
        <div class="m-selected-info">
          <strong>{{ selectedSecurity.market }}:{{ selectedSecurity.symbol }}</strong>
          <span>{{ selectedSecurity.name ?? '' }}</span>
          <span v-if="!accountMatchesSecurity" class="m-currency-warn">需 {{ requiredCurrency }} 账户</span>
          <span v-else-if="!selectedLots.length" class="m-section-sub">无未结清批次</span>
        </div>
        <div class="m-selected-actions">
          <el-select v-if="accounts.length" v-model="selectedAccountId" size="small" class="m-account-select" placeholder="选择账户">
            <el-option v-for="account in accounts" :key="account.id" :label="`${account.name} (${account.currency})`" :value="account.id" />
          </el-select>
          <el-button size="small" :disabled="!accountMatchesSecurity" @click="buyVisible = true">买入记账</el-button>
          <el-button size="small" type="danger" :disabled="!accountMatchesSecurity || !selectedLots.length" @click="sellVisible = true">卖出配对</el-button>
          <el-button v-if="!accounts.some((item) => item.currency === requiredCurrency)" size="small" @click="createAccountForCurrency(requiredCurrency)">创建 {{ requiredCurrency }} 账户</el-button>
        </div>
      </div>
    </section>

    <div class="m-positions-wrap">
      <ActivePositionList
        :positions="positions"
        :total-open-positions="dashboard?.summary.totalOpenPositions ?? 0"
        :currency="dashboardCurrency"
        :account-id="dashboard?.accountId ?? ''"
        :loading="ledger.loading"
        :pending-scopes="pendingScopes"
        :unlisted-scopes="unlistedScopes"
        @select="openPosition"
        @recalculate="recalculate"
      />
    </div>

    <section class="m-section">
      <div class="m-section-head"><span>自选股行情</span></div>
      <div v-if="dashboard?.quotes.length" class="m-quote-grid">
        <div v-for="quote in dashboard.quotes" :key="quote.symbol" class="m-quote-card">
          <div class="m-quote-symbol">{{ quote.symbol }}</div>
          <QuoteBadge :quote="quote" :currency="quote.currency ?? dashboardCurrency" compact />
        </div>
      </div>
      <div v-else class="m-empty">暂无自选股行情</div>
    </section>
  </template>

  <BuyTradeDialog v-if="selectedSecurity" v-model="buyVisible" :security-id="selectedSecurity.id" :account-id="selectedAccountId" :currency="selectedSecurity.currency" :is-fund="isFundSecurity(selectedSecurity)" @saved="reloadQuickTrade" />
  <SellMatchDrawer v-if="selectedSecurity" v-model="sellVisible" :security-id="selectedSecurity.id" :account-id="selectedAccountId" :currency="selectedSecurity.currency" :is-fund="isFundSecurity(selectedSecurity)" :lots="selectedLots" @saved="reloadQuickTrade" />
  <ProfitSettingDialog v-model="globalTargetVisible" />
  <NotificationSettingDialog v-model="notificationSettingVisible" />
</template>

<style scoped>
.dashboard-metrics { grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); }
.dashboard-workspace {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 20px;
}
.workspace-positions {
  flex: 0 0 520px;
  width: 520px;
  position: sticky;
  top: 16px;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
}
.workspace-detail {
  flex: 1;
  min-width: 0;
}
.workspace-empty-card {
  min-height: 260px;
  display: flex;
  align-items: center;
  justify-content: center;
}
@media (min-width: 769px) and (max-width: 1360px) {
  .workspace-positions {
    flex: 0 0 460px;
    width: 460px;
  }
}
@media (min-width: 769px) and (max-width: 1120px) {
  .dashboard-workspace {
    flex-direction: column;
  }
  .workspace-positions {
    position: static;
    width: 100%;
    flex: none;
    max-height: none;
  }
}
.m-positions-wrap { margin-bottom: 8px; }
.m-dash-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 7px 10px; border-bottom: 1px solid #edf0f5; flex-wrap: wrap; }
.m-dash-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.m-summary-top { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-bottom: 1px solid #edf0f5; }
.m-summary-cell { padding: 8px 10px; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.m-summary-cell:first-child { border-right: 1px solid #edf0f5; }
.m-summary-label { font-size: 11px; color: #7d899b; }
.m-summary-main { font-size: 16px; font-weight: 700; line-height: 1.25; overflow-wrap: anywhere; }
.m-summary-sub { font-size: 10px; color: #8290a3; }
.m-summary-bottom { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.m-summary-mini { padding: 6px 8px; display: flex; flex-direction: column; gap: 2px; min-width: 0; border-right: 1px solid #edf0f5; }
.m-summary-mini:last-child { border-right: 0; }
.m-summary-mini-val { font-size: 12px; font-weight: 700; overflow-wrap: anywhere; }
.m-quick-trade { padding: 8px 10px; }
.m-quick-row { display: flex; gap: 6px; align-items: center; }
.m-quick-row .el-input { flex: 1; min-width: 0; }
.m-security-results { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
.m-security-option { display: flex; justify-content: space-between; align-items: center; gap: 8px; width: 100%; border: 1px solid #e9edf3; border-radius: 6px; background: #fafbfd; padding: 6px 8px; font-size: 12px; text-align: left; cursor: pointer; }
.m-security-option span { color: #64748b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.m-security-create { margin-top: 6px; display: flex; flex-direction: column; gap: 6px; }
.m-security-create-row { display: flex; gap: 6px; align-items: center; }
.m-security-create-row .el-input { flex: 1; min-width: 0; }
.m-selected-box { margin-top: 8px; padding-top: 8px; border-top: 1px solid #edf0f5; display: flex; flex-direction: column; gap: 6px; }
.m-selected-info { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; font-size: 12px; color: #334155; }
.m-currency-warn { color: #d99014; font-size: 11px; }
.m-selected-actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.m-account-select { flex: 1; min-width: 130px; }
.m-quote-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; padding: 8px; }
.m-quote-card { padding: 8px; border: 1px solid #edf0f4; border-radius: 6px; background: #fff; min-width: 0; }
.m-quote-symbol { font-size: 12px; font-weight: 700; margin-bottom: 4px; color: #182230; }
</style>
