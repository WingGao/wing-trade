<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { Account, Currency, DashboardMode, DashboardPosition, Lot, PositionRefreshScope, Security, SecurityCandidate, ValuationStatus } from '../api/types'
import { useLedgerStore } from '../stores/ledger'
import { usePreferencesStore } from '../stores/preferences'
import { api } from '../api/client'
import QuoteBadge from '../components/QuoteBadge.vue'
import BuyTradeDialog from '../components/BuyTradeDialog.vue'
import SellMatchDrawer from '../components/SellMatchDrawer.vue'
import ProfitSettingDialog from '../components/ProfitSettingDialog.vue'
import { formatDateTime, formatDecimal, formatMoney, formatPercent, formatQuantity, isFundSecurity, priceDigits, quantityUnit, trendClass, valuationStatusLabel } from '../utils/format'
import { isPositionRefreshing, refreshPosition } from '../utils/recalculate'
import { useViewport } from '../utils/viewport'

const ledger = useLedgerStore()
const preferences = usePreferencesStore()
const router = useRouter()
const { isMobile } = useViewport()
const positions = computed(() => ledger.positions)
const positionSortOrder = ref<'ascending' | 'descending'>('descending')
const dashboard = computed(() => ledger.dashboard)
const dashboardCurrency = computed<Currency | null>(() => dashboard.value?.currency ?? null)
const securityKeyword = ref('')
const securityResults = ref<SecurityCandidate[]>([])
const searching = ref(false)
const searchError = ref('')
const searched = ref(false)
const globalTargetVisible = ref(false)
const selectedSecurity = ref<Security | null>(null)
const selectedLots = ref<Lot[]>([])
const accounts = ref<Account[]>([])
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
const unlistedScopes = computed(() => pendingScopes.value.filter((scope) => !positions.value.some((position) => position.securityId === scope.securityId && position.strategyId === scope.strategyId)))
const selectedAccount = computed(() => accounts.value.find((account) => account.id === selectedAccountId.value))
const accountMatchesSecurity = computed(() => !selectedSecurity.value || selectedAccount.value?.currency === requiredCurrency.value)
const requiredCurrency = computed<'CNY' | 'USD'>(() => selectedSecurity.value?.currency ?? (selectedSecurity.value?.market === 'US' ? 'USD' : 'CNY'))

/** 读取当前涨幅用于排序，无有效行情时返回空值。 */
function positionChangeValue(position: DashboardPosition): number | null {
  const value = Number(position.quote?.changePct)
  return Number.isFinite(value) ? value : null
}

/** 按当前涨幅排列活动仓位，并将无有效行情的持仓始终放在末尾。 */
const sortedPositions = computed(() => {
  const direction = positionSortOrder.value === 'ascending' ? 1 : -1
  return [...positions.value].sort((left, right) => {
    const leftChange = positionChangeValue(left)
    const rightChange = positionChangeValue(right)
    if (leftChange === null && rightChange === null) return 0
    if (leftChange === null) return 1
    if (rightChange === null) return -1
    return (leftChange - rightChange) * direction
  })
})

/** 接收表格列排序变化并切换当前涨幅排序方向。 */
function handlePositionSort({ prop, order }: { prop: string; order: 'ascending' | 'descending' | null }) {
  if (prop !== 'quote.changePct') return
  positionSortOrder.value = order === 'ascending' ? 'ascending' : 'descending'
}

/** 移动端点击排序头在当前涨幅降序与升序之间切换。 */
function togglePositionSortOrder(): void {
  positionSortOrder.value = positionSortOrder.value === 'descending' ? 'ascending' : 'descending'
}

/** 按当前持仓数量和最新报价计算展示用总价值，缺少有效行情时返回空值。 */
function currentPositionValue(position: DashboardPosition): string | null {
  const price = position.quote?.lastPrice
  if (price === null || price === undefined || price === '') return null
  const quantity = Number(position.remainingQty)
  const latestPrice = Number(price)
  if (!Number.isFinite(quantity) || !Number.isFinite(latestPrice)) return null
  const value = quantity * latestPrice
  return Number.isFinite(value) ? value.toFixed(2) : null
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

/** 加载账户列表并保留当前账户选择。 */
async function loadAccounts() {
  try {
    const result = await api.accounts()
    accounts.value = result
    if (!selectedAccountId.value) selectedAccountId.value = result.find((account) => account.currency === 'CNY')?.id ?? result[0]?.id ?? ''
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '加载账户失败')
  }
}

/** 清理自动轮询计时器，确保隐藏或重载时不会叠加任务。 */
function clearPollTimer() {
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = null
}

/** 按服务器返回的间隔安排下一次自动估值，避免前端推断市场时区。 */
function schedulePoll(seconds: number | undefined) {
  clearPollTimer()
  if (!pageAlive.value || document.hidden || mutationActive.value) return
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
  const result = await ledger.loadDashboard({
    accountId: dashboard.value?.accountId,
    includeClosed: preferences.includeClosed,
    mode,
    reuse: mode === 'auto' && !afterWrite,
  })
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

/** 创建当前证券币种对应的账户，并刷新批次列表。 */
async function createAccountForCurrency(currency: 'CNY' | 'USD') {
  try {
    const result = await ElMessageBox.prompt(`请输入 ${currency} 账户名称`, `创建 ${currency} 账户`, { inputValue: currency === 'USD' ? 'US Trading' : 'A股 Trading' })
    const account = await api.createAccount({ name: result.value, currency })
    accounts.value.push(account)
    selectedAccountId.value = account.id
    ElMessage.success(`${currency} 账户已创建`)
    if (selectedSecurity.value) selectedLots.value = (await api.lots(selectedSecurity.value.id, false, account.id)).lots
  } catch {
    // 用户取消或接口失败时不改变当前记录。
  }
}

/** 记账成功后重载整份估值，同时保留“显示已结清”和当前批次选择。 */
async function reloadQuickTrade() {
  ledger.invalidateDashboard()
  await loadValuation('auto', false, true, true)
  if (selectedSecurity.value) {
    selectedLots.value = (await api.lots(selectedSecurity.value.id, false, selectedAccountId.value)).lots
  }
}

/** 根据持仓内置证券标识打开详情页，不再依赖独立行情状态。 */
async function openPosition(position: DashboardPosition) {
  const account = dashboard.value?.accountId
  if (position.market && position.symbol) {
    router.push({ path: `/security/${position.market}/${position.symbol}`, query: { accountId: account, strategyId: position.strategyId } })
    return
  }
  const securities = await api.securities()
  const security = securities.find((item) => item.id === position.securityId)
  if (security && pageAlive.value && account === dashboard.value?.accountId) router.push({ path: `/security/${security.market}/${security.symbol}`, query: { accountId: account, strategyId: position.strategyId } })
  else ElMessage.warning('找不到证券基础信息')
}

/** 将行操作绑定看板快照账户，而不是快速记账选择器中的账户。 */
function rowScope(position: DashboardPosition): PositionRefreshScope {
  return { accountId: dashboard.value?.accountId ?? '', securityId: position.securityId, strategyId: position.strategyId, market: position.market, symbol: position.symbol, name: position.name }
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
      // 过滤条件变化只替代读取，不取消已提交的重算；稳定后再报告读取结果。
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

/** 首次进入先显示缓存，再请求自动估值，并注册页面生命周期。 */
async function initializePage() {
  await loadValuation('cache', false, false)
  if (!document.hidden) await loadValuation('auto')
}

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
  void loadAccounts()
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
      <el-tag :type="statusTagType(valuationStatus)">{{ valuationStatusLabel(valuationStatus) }}</el-tag>
      <el-button @click="globalTargetVisible = true">全局止盈设置</el-button>
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
    <el-card class="metric-card"><span class="metric-label">累计买入现金流</span><div class="metric-value">{{ formatMoney(ledger.summary?.totalBuyCashOut, dashboardCurrency) }}</div></el-card>
    <el-card class="metric-card"><span class="metric-label">累计卖出回款</span><div class="metric-value">{{ formatMoney(ledger.summary?.totalSellCashIn, dashboardCurrency) }}</div></el-card>
    <el-card class="metric-card"><span class="metric-label">已实现收益</span><div :class="['metric-value', trendClass(ledger.summary?.realizedPnl)]">{{ formatMoney(ledger.summary?.realizedPnl, dashboardCurrency) }}</div></el-card>
    <el-card class="metric-card">
      <span class="metric-label">账户未实现浮盈（参考）</span>
      <div :class="['metric-value', trendClass(ledger.summary?.unrealizedPnl)]">{{ unrealizedValue }}</div>
      <div class="metric-detail">{{ unrealizedRate }} · 未扣预计卖出费用</div>
    </el-card>
  </div>

  <el-card class="panel">
    <template #header><div class="panel-title">活动仓位 <span class="muted">{{ dashboard?.summary.totalOpenPositions ?? 0 }} 个未结清持仓</span></div></template>
    <el-alert v-if="pendingScopes.length" type="warning" :closable="false" show-icon class="status-alert" title="需要刷新：部分交易流水已更正，相关持仓、收益及账户汇总尚未重新核算。" />
    <div v-for="scope in unlistedScopes" :key="`${scope.accountId}|${scope.securityId}|${scope.strategyId}`" class="pending-refresh-scope">
      <span><strong>{{ scope.market }}:{{ scope.symbol }}</strong> {{ scope.name ?? '' }} · 当前无活动仓位 · 需要刷新</span>
      <el-button type="warning" :loading="isPositionRefreshing(scope)" @click="recalculate(scope)">刷新持仓</el-button>
    </div>
    <el-table :data="sortedPositions" :default-sort="{ prop: 'quote.changePct', order: 'descending' }" v-loading="ledger.loading" stripe empty-text="暂无持仓" class="position-table" @sort-change="handlePositionSort">
      <el-table-column label="证券" min-width="180">
        <template #default="{ row }"><a class="security-link" href="#" @click.prevent="openPosition(row)"><strong>{{ row.market ? `${row.market}:${row.symbol}` : row.securityId }}</strong><span v-if="row.name" class="position-name">{{ row.name }}</span></a></template>
      </el-table-column>
      <el-table-column label="持仓" min-width="150">
        <template #default="{ row }"><div class="position-holding"><strong class="position-quantity">{{ formatQuantity(row.remainingQty, isFundSecurity(row)) }} {{ quantityUnit(row) }}</strong><span class="position-cost">加权成本 {{ formatMoney(row.averageCost, row.currency ?? dashboardCurrency, priceDigits(row, 2)) }}</span></div></template>
      </el-table-column>
      <el-table-column prop="quote.changePct" label="当前总价值" min-width="220" sortable="custom" :sort-orders="['descending', 'ascending']"><template #default="{ row }"><QuoteBadge :quote="row.quote" :currency="row.currency ?? dashboardCurrency" :display-value="currentPositionValue(row)" compact /></template></el-table-column>
      <el-table-column label="持仓浮盈" min-width="135">
        <template #default="{ row }"><div :class="['pnl-value', trendClass(row.unrealizedPnl)]">{{ formatMoney(row.unrealizedPnl, row.currency ?? dashboardCurrency) }}</div><div :class="['pnl-rate', trendClass(row.unrealizedReturnPct)]">{{ formatPercent(row.unrealizedReturnPct) }}</div></template>
      </el-table-column>
      <el-table-column label="最低价批次合计" min-width="235">
        <template #default="{ row }">
          <div v-if="row.lowestPriceGroup" class="lowest-group">
            <div>共同买入价 <strong>{{ formatMoney(row.lowestPriceGroup.purchasePrice, row.currency ?? dashboardCurrency, priceDigits(row, 2)) }}</strong></div>
            <div class="lowest-group-meta"><span>{{ row.lowestPriceGroup.lotCount }}批</span><span>{{ formatQuantity(row.lowestPriceGroup.remainingQty, isFundSecurity(row)) }}{{ quantityUnit(row) }}</span><span>{{ formatDecimal(row.lowestPriceGroup.remainingCost, 2) }}</span></div>
            <div :class="['group-pnl', trendClass(row.lowestPriceGroup.unrealizedPnl)]">组浮盈 {{ formatMoney(row.lowestPriceGroup.unrealizedPnl, row.currency ?? dashboardCurrency) }} / {{ formatPercent(row.lowestPriceGroup.unrealizedReturnPct) }}</div>
          </div>
          <span v-else class="muted">无符合条件的未结清批次</span>
        </template>
      </el-table-column>
      <el-table-column label="策略回本参考价" min-width="135"><template #default="{ row }">{{ formatMoney(row.strategyBreakevenPrice, row.currency ?? dashboardCurrency, priceDigits(row, 2)) }}</template></el-table-column>
      <el-table-column label="持仓核算" width="150" fixed="right"><template #default="{ row }"><div v-if="row.needsRefresh"><el-tag type="warning">需要刷新</el-tag><el-button link type="warning" :loading="isPositionRefreshing(rowScope(row))" @click="recalculate(rowScope(row))">刷新持仓</el-button></div><span v-else class="muted">—</span></template></el-table-column>
    </el-table>
  </el-card>

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
          <el-switch v-model="preferences.includeClosed" size="small" active-text="已结清" />
          <el-button size="small" @click="globalTargetVisible = true">全局止盈</el-button>
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
          <strong :class="['m-summary-main', 'num', trendClass(ledger.summary?.unrealizedPnl)]">{{ unrealizedValue }}</strong>
          <span class="m-summary-sub num">{{ unrealizedRate }}</span>
        </div>
      </div>
      <div class="m-summary-bottom">
        <div class="m-summary-mini">
          <span class="m-summary-label">累计买入</span>
          <strong class="m-summary-mini-val num">{{ formatMoney(ledger.summary?.totalBuyCashOut, dashboardCurrency) }}</strong>
        </div>
        <div class="m-summary-mini">
          <span class="m-summary-label">累计卖出</span>
          <strong class="m-summary-mini-val num">{{ formatMoney(ledger.summary?.totalSellCashIn, dashboardCurrency) }}</strong>
        </div>
        <div class="m-summary-mini">
          <span class="m-summary-label">已实现收益</span>
          <strong :class="['m-summary-mini-val', 'num', trendClass(ledger.summary?.realizedPnl)]">{{ formatMoney(ledger.summary?.realizedPnl, dashboardCurrency) }}</strong>
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

    <section class="m-section m-positions" v-loading="ledger.loading">
      <div class="m-section-head">
        <span>活动仓位</span>
        <span class="m-section-sub">{{ dashboard?.summary.totalOpenPositions ?? 0 }} 个未结清持仓</span>
      </div>
      <div v-if="pendingScopes.length" class="m-compact-note is-warning">需要刷新：部分交易流水已更正，相关持仓、收益及账户汇总尚未重新核算。</div>
      <div v-for="scope in unlistedScopes" :key="`${scope.accountId}|${scope.securityId}|${scope.strategyId}`" class="m-pending-scope">
        <span class="m-pending-text"><strong>{{ scope.market }}:{{ scope.symbol }}</strong> {{ scope.name ?? '' }} · 无活动仓位 · 待刷新</span>
        <el-button size="small" type="warning" :loading="isPositionRefreshing(scope)" @click="recalculate(scope)">刷新持仓</el-button>
      </div>
      <div class="m-pos-header">
        <span>证券 / 持仓</span>
        <button type="button" class="m-sort-btn" @click="togglePositionSortOrder">
          当前总价值 <span aria-hidden="true">{{ positionSortOrder === 'descending' ? '↓' : '↑' }}</span>
        </button>
        <span class="m-align-right">持仓浮盈</span>
      </div>
      <div v-if="sortedPositions.length" class="m-position-list">
        <article v-for="row in sortedPositions" :key="`${row.securityId}|${row.strategyId}`" class="m-pos-item">
          <div class="m-pos-main">
            <div class="m-pos-col-sec">
              <a class="m-pos-link" href="#" @click.prevent="openPosition(row)">
                <strong>{{ row.market ? `${row.market}:${row.symbol}` : row.securityId }}</strong>
                <span v-if="row.name" class="m-pos-name">{{ row.name }}</span>
              </a>
              <div class="m-pos-holding num">{{ formatQuantity(row.remainingQty, isFundSecurity(row)) }}{{ quantityUnit(row) }} · 均 {{ formatMoney(row.averageCost, row.currency ?? dashboardCurrency, priceDigits(row, 2)) }}</div>
            </div>
            <div class="m-pos-col-quote">
              <QuoteBadge :quote="row.quote" :currency="row.currency ?? dashboardCurrency" :display-value="currentPositionValue(row)" compact />
            </div>
            <div class="m-pos-col-pnl">
              <div :class="['m-pos-pnl', 'num', trendClass(row.unrealizedPnl)]">{{ formatMoney(row.unrealizedPnl, row.currency ?? dashboardCurrency) }}</div>
              <div :class="['m-pos-rate', 'num', trendClass(row.unrealizedReturnPct)]">{{ formatPercent(row.unrealizedReturnPct) }}</div>
            </div>
          </div>
          <div class="m-pos-foot">
            <div class="m-pos-lowest num">
              <template v-if="row.lowestPriceGroup">
                最低批 {{ formatMoney(row.lowestPriceGroup.purchasePrice, row.currency ?? dashboardCurrency, priceDigits(row, 2)) }} · {{ row.lowestPriceGroup.lotCount }}批 {{ formatQuantity(row.lowestPriceGroup.remainingQty, isFundSecurity(row)) }}{{ quantityUnit(row) }} {{ formatDecimal(row.lowestPriceGroup.remainingCost, 2) }}
              </template>
              <span v-else class="m-section-sub">无最低批</span>
            </div>
            <div v-if="row.lowestPriceGroup || row.needsRefresh" class="m-pos-foot-right">
              <span v-if="row.lowestPriceGroup" :class="['m-pos-group-pnl', 'num', trendClass(row.lowestPriceGroup.unrealizedPnl)]">{{ formatMoney(row.lowestPriceGroup.unrealizedPnl, row.currency ?? dashboardCurrency) }} / {{ formatPercent(row.lowestPriceGroup.unrealizedReturnPct) }}</span>
              <el-button v-if="row.needsRefresh" size="small" type="warning" plain :loading="isPositionRefreshing(rowScope(row))" @click="recalculate(rowScope(row))">刷新持仓</el-button>
            </div>
          </div>
        </article>
      </div>
      <div v-else class="m-empty">暂无持仓</div>
    </section>

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
</template>

<style scoped>
.dashboard-metrics { grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); }
.pending-refresh-scope { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 0; flex-wrap: wrap; }
.position-holding { display: flex; flex-direction: column; gap: 3px; line-height: 1.45; }
.position-quantity { color: #263244; }
.position-cost { color: #7d899b; font-size: 12px; }
.lowest-group-meta { display: flex; gap: 12px; margin: 2px 0; color: #7d899b; font-size: 12px; white-space: nowrap; }
.m-dash-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 7px 10px; border-bottom: 1px solid #edf0f5; flex-wrap: wrap; }
.m-dash-actions { display: flex; align-items: center; gap: 6px; }
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
.m-pending-scope { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 10px; background: #fffbf2; border-bottom: 1px solid #f5e6c8; font-size: 11px; }
.m-pending-text { min-width: 0; overflow-wrap: anywhere; }
.m-pos-header { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1.25fr) minmax(0, 0.85fr); gap: 6px; align-items: center; padding: 6px 10px; background: #f8fafc; border-bottom: 1px solid #edf0f5; font-size: 11px; font-weight: 600; color: #64748b; }
.m-sort-btn { justify-self: end; border: 0; background: transparent; padding: 0; font-size: 11px; font-weight: 700; color: #2563eb; cursor: pointer; display: inline-flex; align-items: center; gap: 2px; }
.m-align-right { text-align: right; }
.m-position-list { display: flex; flex-direction: column; }
.m-pos-item { border-bottom: 1px solid #e9edf3; }
.m-pos-item:last-child { border-bottom: 0; }
.m-pos-main { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1.25fr) minmax(0, 0.85fr); gap: 6px; align-items: start; padding: 8px 10px 6px; }
.m-pos-col-sec { min-width: 0; }
.m-pos-link { display: flex; flex-direction: column; text-decoration: none; color: #1d4ed8; font-size: 13px; line-height: 1.25; }
.m-pos-name { font-size: 11px; font-weight: 400; color: #64748b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.m-pos-holding { margin-top: 3px; font-size: 11px; color: #475569; line-height: 1.3; }
.m-pos-col-quote { min-width: 0; display: flex; justify-content: flex-end; text-align: right; }
.m-pos-col-quote :deep(.quote-value-row--compact) { justify-content: flex-end; }
.m-pos-col-pnl { min-width: 0; text-align: right; }
.m-pos-pnl { font-size: 13px; font-weight: 700; line-height: 1.25; }
.m-pos-rate { font-size: 11px; margin-top: 2px; }
.m-pos-foot { display: flex; align-items: center; justify-content: space-between; gap: 6px; flex-wrap: wrap; padding: 5px 10px 7px; background: #fafbfd; border-top: 1px dashed #edf0f5; font-size: 11px; color: #5b687c; line-height: 1.35; }
.m-pos-lowest { min-width: 0; flex: 1; }
.m-pos-foot-right { display: flex; align-items: center; gap: 6px; flex-shrink: 0; margin-left: auto; }
.m-pos-group-pnl { font-weight: 600; white-space: nowrap; }
.m-quote-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; padding: 8px; }
.m-quote-card { padding: 8px; border: 1px solid #edf0f4; border-radius: 6px; background: #fff; min-width: 0; }
.m-quote-symbol { font-size: 12px; font-weight: 700; margin-bottom: 4px; color: #182230; }
</style>
