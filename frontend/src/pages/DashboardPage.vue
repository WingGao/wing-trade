<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { Account, Currency, DashboardMode, DashboardPosition, Lot, Security, SecurityCandidate, ValuationStatus } from '../api/types'
import { useLedgerStore } from '../stores/ledger'
import { usePreferencesStore } from '../stores/preferences'
import { api } from '../api/client'
import QuoteBadge from '../components/QuoteBadge.vue'
import BuyTradeDialog from '../components/BuyTradeDialog.vue'
import SellMatchDrawer from '../components/SellMatchDrawer.vue'
import ProfitSettingDialog from '../components/ProfitSettingDialog.vue'
import { formatDateTime, formatMoney, formatPercent, formatQuantity, trendClass, valuationStatusLabel } from '../utils/format'

const ledger = useLedgerStore()
const preferences = usePreferencesStore()
const router = useRouter()
const positions = computed(() => ledger.positions)
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
const marketChoice = ref<'CN' | 'US'>('CN')
const securityName = ref('')
const buyVisible = ref(false)
const sellVisible = ref(false)
const pageAlive = ref(false)
const manualRefreshing = ref(false)
const selectedAccount = computed(() => accounts.value.find((account) => account.id === selectedAccountId.value))
const accountMatchesSecurity = computed(() => !selectedSecurity.value || selectedAccount.value?.currency === requiredCurrency.value)
const requiredCurrency = computed<'CNY' | 'USD'>(() => selectedSecurity.value?.currency ?? (selectedSecurity.value?.market === 'US' ? 'USD' : 'CNY'))

let pollTimer: number | null = null
let responseGeneration = 0
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
  if (!pageAlive.value || document.hidden) return
  const parsed = Number(seconds)
  const delaySeconds = Number.isFinite(parsed) ? Math.max(1, parsed) : 60
  pollTimer = window.setTimeout(() => {
    pollTimer = null
    void loadValuation('auto')
  }, delaySeconds * 1000)
}

/** 通过同一首页接口加载整份估值，并用代次防止旧响应覆盖新选择。 */
async function loadValuation(mode: DashboardMode, reportSuccess = false, scheduleNext = true) {
  const requestId = ++responseGeneration
  clearPollTimer()
  const result = await ledger.loadDashboard({
    includeClosed: preferences.includeClosed,
    mode,
    reuse: mode === 'auto',
  })
  if (!pageAlive.value || requestId !== responseGeneration) return
  if (scheduleNext) schedulePoll(result?.pollAfterSeconds)
  if (reportSuccess && result) ElMessage.success(mode === 'manual' ? '估值与行情刷新完成' : '首页数据已更新')
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
    const market = raw.includes(':') ? raw.split(':')[0] : marketChoice.value === 'US' ? 'US' : /^(000|001|002|003|300|301)\d{3}$/.test(symbol) ? 'SZ' : /^(4|8|92)/.test(symbol) ? 'BJ' : 'SH'
    const security = await api.createSecurity({ market, symbol, name: securityName.value.trim() })
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
    security = candidate.id ? { ...candidate, id: candidate.id } : await api.createSecurity({ market: candidate.market, symbol: candidate.symbol, name: candidate.name })
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
  await loadValuation('auto')
  if (selectedSecurity.value) {
    selectedLots.value = (await api.lots(selectedSecurity.value.id, false, selectedAccountId.value)).lots
  }
}

/** 根据持仓内置证券标识打开详情页，不再依赖独立行情状态。 */
async function openPosition(position: DashboardPosition) {
  if (position.market && position.symbol) {
    router.push(`/security/${position.market}/${position.symbol}`)
    return
  }
  const securities = await api.securities()
  const security = securities.find((item) => item.id === position.securityId)
  if (security) router.push(`/security/${security.market}/${security.symbol}`)
  else ElMessage.warning('找不到证券基础信息')
}

/** 首次进入先显示缓存，再请求自动估值，并注册页面生命周期。 */
async function initializePage() {
  await loadValuation('cache', false, false)
  if (!document.hidden) await loadValuation('auto')
}

watch(() => preferences.includeClosed, () => {
  if (pageAlive.value) void loadValuation('auto')
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
  clearPollTimer()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <section class="page-head">
    <div><h2>账户总览</h2><p class="muted">以真实交易流水为准，价格、浮盈和汇总来自同一次估值响应。</p></div>
    <div class="toolbar">
      <el-tag :type="statusTagType(valuationStatus)">{{ valuationStatusLabel(valuationStatus) }}</el-tag>
      <el-button @click="globalTargetVisible = true">全局止盈设置</el-button>
      <el-switch v-model="preferences.includeClosed" active-text="显示已结清" />
      <el-button type="primary" :loading="manualRefreshing" @click="refresh">刷新行情</el-button>
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
      <el-select v-model="marketChoice" style="width:90px"><el-option label="A股" value="CN" /><el-option label="美股" value="US" /></el-select>
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
    <el-table :data="positions" v-loading="ledger.loading" stripe empty-text="暂无持仓" class="position-table">
      <el-table-column label="证券" min-width="180">
        <template #default="{ row }"><a class="security-link" href="#" @click.prevent="openPosition(row)"><strong>{{ row.market ? `${row.market}:${row.symbol}` : row.securityId }}</strong><span v-if="row.name" class="position-name">{{ row.name }}</span></a></template>
      </el-table-column>
      <el-table-column label="持股数" min-width="100"><template #default="{ row }">{{ formatQuantity(row.remainingQty) }}</template></el-table-column>
      <el-table-column label="加权成本" min-width="120"><template #default="{ row }">{{ formatMoney(row.averageCost, row.currency ?? dashboardCurrency) }}</template></el-table-column>
      <el-table-column label="当前价 / 当日涨幅" min-width="220"><template #default="{ row }"><QuoteBadge :quote="row.quote" :currency="row.currency ?? dashboardCurrency" /></template></el-table-column>
      <el-table-column label="持仓浮盈" min-width="135">
        <template #default="{ row }"><div :class="['pnl-value', trendClass(row.unrealizedPnl)]">{{ formatMoney(row.unrealizedPnl, row.currency ?? dashboardCurrency) }}</div><div :class="['pnl-rate', trendClass(row.unrealizedReturnPct)]">{{ formatPercent(row.unrealizedReturnPct) }}</div></template>
      </el-table-column>
      <el-table-column label="最低价批次合计" min-width="235">
        <template #default="{ row }">
          <div v-if="row.lowestPriceGroup" class="lowest-group">
            <div>共同买入价 <strong>{{ formatMoney(row.lowestPriceGroup.purchasePrice, row.currency ?? dashboardCurrency) }}</strong></div>
            <div class="muted">{{ row.lowestPriceGroup.lotCount }} 个批次 · 合计剩余 {{ formatQuantity(row.lowestPriceGroup.remainingQty) }} 股 · 成本 {{ formatMoney(row.lowestPriceGroup.remainingCost, row.currency ?? dashboardCurrency) }}</div>
            <div :class="['group-pnl', trendClass(row.lowestPriceGroup.unrealizedPnl)]">组浮盈 {{ formatMoney(row.lowestPriceGroup.unrealizedPnl, row.currency ?? dashboardCurrency) }} / {{ formatPercent(row.lowestPriceGroup.unrealizedReturnPct) }}</div>
          </div>
          <span v-else class="muted">无符合条件的未结清批次</span>
        </template>
      </el-table-column>
      <el-table-column label="策略回本参考价" min-width="135"><template #default="{ row }">{{ formatMoney(row.strategyBreakevenPrice, row.currency ?? dashboardCurrency) }}</template></el-table-column>
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

  <BuyTradeDialog v-if="selectedSecurity" v-model="buyVisible" :security-id="selectedSecurity.id" :account-id="selectedAccountId" :currency="selectedSecurity.currency" @saved="reloadQuickTrade" />
  <SellMatchDrawer v-if="selectedSecurity" v-model="sellVisible" :security-id="selectedSecurity.id" :account-id="selectedAccountId" :currency="selectedSecurity.currency" :lots="selectedLots" @saved="reloadQuickTrade" />
  <ProfitSettingDialog v-model="globalTargetVisible" />
</template>

<style scoped>
.dashboard-metrics { grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); }
</style>
