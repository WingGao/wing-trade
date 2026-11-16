<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { Account, Lot, Security } from '../api/types'
import { useLedgerStore } from '../stores/ledger'
import { useQuotesStore } from '../stores/quotes'
import { usePreferencesStore } from '../stores/preferences'
import { api } from '../api/client'
import QuoteBadge from '../components/QuoteBadge.vue'
import BuyTradeDialog from '../components/BuyTradeDialog.vue'
import SellMatchDrawer from '../components/SellMatchDrawer.vue'
const ledger = useLedgerStore(); const quotes = useQuotesStore(); const preferences = usePreferencesStore(); const router = useRouter()
const positions = computed(() => ledger.positions)
const securityKeyword = ref('')
const securityResults = ref<Security[]>([])
const selectedSecurity = ref<Security | null>(null)
const selectedLots = ref<Lot[]>([])
const accounts = ref<Account[]>([])
const selectedAccountId = ref('')
const marketChoice = ref<'CN' | 'US'>('CN')
const securityName = ref('')
const buyVisible = ref(false)
const sellVisible = ref(false)
const selectedAccount = computed(() => accounts.value.find((account) => account.id === selectedAccountId.value))
const accountMatchesSecurity = computed(() => !selectedSecurity.value || selectedAccount.value?.currency === selectedSecurity.value.currency)
const requiredCurrency = computed<'CNY' | 'USD'>(() => selectedSecurity.value?.currency ?? (selectedSecurity.value?.market === 'US' ? 'USD' : 'CNY'))
onMounted(async () => { await ledger.loadDashboard(); await quotes.load(); accounts.value = await api.accounts(); selectedAccountId.value = accounts.value.find((item) => item.currency === 'CNY')?.id ?? accounts.value[0]?.id ?? '' })
async function refresh() { await quotes.refresh(); ElMessage.success('行情刷新请求已完成') }
async function searchSecurities() {
  if (!securityKeyword.value.trim()) { securityResults.value = []; return }
  const symbol = securityKeyword.value.trim().split(/[:\s]/)[0]
  if (/^[A-Za-z]+$/.test(symbol)) marketChoice.value = 'US'
  else marketChoice.value = 'CN'
  try { securityResults.value = await api.securities(securityKeyword.value.trim()) }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '证券搜索失败') }
}
async function createSecurity() {
  const symbol = securityKeyword.value.trim().split(/[:\s]/)[0].toUpperCase()
  if (!symbol) return
  try {
    const market = marketChoice.value === 'US' ? 'US' : /^(000|001|002|003|300|301)\d{3}$/.test(symbol) ? 'SZ' : 'SH'
    const security = await api.createSecurity({ market, symbol, name: securityName.value.trim() || symbol })
    await chooseSecurity(security)
    ElMessage.success(`已添加 ${security.market}:${security.symbol}`)
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '新增证券失败') }
}
async function chooseSecurity(security: Security) {
  selectedSecurity.value = security
  securityKeyword.value = `${security.market}:${security.symbol} ${security.name ?? ''}`.trim()
  securityResults.value = []
  selectedAccountId.value = accounts.value.find((item) => item.currency === (security.currency ?? (security.market === 'US' ? 'USD' : 'CNY')))?.id ?? selectedAccountId.value
  try { selectedLots.value = (await api.lots(security.id, false, selectedAccountId.value)).lots }
  catch { selectedLots.value = [] }
}
async function createAccountForCurrency(currency: 'CNY' | 'USD') {
  try {
    const result = await ElMessageBox.prompt(`请输入 ${currency} 账户名称`, `创建 ${currency} 账户`, { inputValue: currency === 'USD' ? 'US Trading' : 'A股 Trading' })
    const account = await api.createAccount({ name: result.value, currency })
    accounts.value.push(account); selectedAccountId.value = account.id; ElMessage.success(`${currency} 账户已创建`)
    if (selectedSecurity.value) selectedLots.value = (await api.lots(selectedSecurity.value.id, false, account.id)).lots
  } catch { /* cancelled */ }
}
async function reloadQuickTrade() {
  await ledger.loadDashboard()
  if (selectedSecurity.value) selectedLots.value = (await api.lots(selectedSecurity.value.id, false, selectedAccountId.value)).lots
}
async function openPosition(position: { securityId: string }) {
  const quote = quotes.bySecurity(position.securityId)
  const symbol = quote?.symbol?.split(':')[1]
  if (symbol) {
    router.push(`/security/${quote.symbol.split(':')[0]}/${symbol}`)
    return
  }
  const securities = await api.securities()
  const security = securities.find((item) => item.id === position.securityId)
  if (security) router.push(`/security/${security.market}/${security.symbol}`)
  else ElMessage.warning('找不到证券基础信息')
}
</script>
<template>
  <section class="page-head"><div><h2>账户总览</h2><p class="muted">以真实交易流水为准，行情只用于估值和提醒。</p></div><div class="toolbar"><el-switch v-model="preferences.includeClosed" active-text="显示已结清" @change="() => ledger.loadDashboard(preferences.includeClosed)" /><el-button type="primary" :loading="quotes.loading" @click="refresh">刷新行情</el-button></div></section>
  <el-alert v-if="ledger.error" :title="ledger.error" type="warning" show-icon closable />
  <el-card class="panel quick-trade"><template #header><div class="panel-title">快速记账</div></template><div class="search-row"><el-input v-model="securityKeyword" placeholder="输入证券代码、名称，例如 600519" clearable @keyup.enter="searchSecurities" /><el-button type="primary" @click="searchSecurities">查找证券</el-button><el-select v-if="accounts.length" v-model="selectedAccountId" style="width:190px" placeholder="选择账户"><el-option v-for="account in accounts" :key="account.id" :label="`${account.name} (${account.currency})`" :value="account.id" /></el-select><el-button v-if="selectedSecurity" :disabled="!accountMatchesSecurity" @click="buyVisible = true">买入记账</el-button><el-button v-if="selectedSecurity" type="danger" :disabled="!accountMatchesSecurity || !selectedLots.length" @click="sellVisible = true">卖出配对</el-button><el-button v-if="selectedSecurity && !accounts.some((item) => item.currency === requiredCurrency)" @click="createAccountForCurrency(requiredCurrency)">创建 {{ requiredCurrency }} 账户</el-button></div><div v-if="securityResults.length" class="security-results"><button v-for="security in securityResults" :key="security.id" class="security-option" type="button" @click="chooseSecurity(security)"><strong>{{ security.market }}:{{ security.symbol }}</strong><span>{{ security.name ?? '未命名证券' }}</span></button></div><div v-if="securityKeyword && !securityResults.length && !selectedSecurity" class="security-create"><span class="muted">本地证券库暂无匹配项，可先新增：</span><el-select v-model="marketChoice" style="width:90px"><el-option label="A股" value="CN" /><el-option label="美股" value="US" /></el-select><el-input v-model="securityName" placeholder="证券名称（可选）" /><el-button type="success" @click="createSecurity">新增证券</el-button></div><div v-if="selectedSecurity" class="selected-security">当前证券：<strong>{{ selectedSecurity.market }}:{{ selectedSecurity.symbol }}</strong> {{ selectedSecurity.name ?? '' }}<span v-if="!accountMatchesSecurity" class="currency-warning">当前账户为 {{ selectedAccount?.currency ?? '未选择' }}，请选择 {{ requiredCurrency }} 账户</span><span v-else-if="!selectedLots.length" class="muted">暂无未结清批次，可直接录入买入</span></div></el-card>
  <div class="metric-grid"><el-card class="metric-card"><span class="metric-label">累计买入现金流</span><div class="metric-value">¥ {{ ledger.summary?.totalBuyCashOut ?? '—' }}</div></el-card><el-card class="metric-card"><span class="metric-label">累计卖出回款</span><div class="metric-value">¥ {{ ledger.summary?.totalSellCashIn ?? '—' }}</div></el-card><el-card class="metric-card"><span class="metric-label">已实现收益</span><div class="metric-value positive">¥ {{ ledger.summary?.realizedPnl ?? '—' }}</div></el-card><el-card class="metric-card"><span class="metric-label">未实现毛浮盈（参考）</span><div class="metric-value" :class="Number(ledger.summary?.unrealizedPnl ?? 0) >= 0 ? 'positive' : 'negative'">¥ {{ ledger.summary?.unrealizedPnl ?? '行情不可用' }}</div></el-card></div>
  <el-card class="panel"><template #header><div class="panel-title">活动仓位</div></template><el-table :data="positions" v-loading="ledger.loading" stripe empty-text="暂无持仓"><el-table-column label="证券" min-width="180"><template #default="{ row }"><a class="security-link" href="#" @click.prevent="openPosition(row)"><strong>{{ row.market ? `${row.market}:${row.symbol}` : row.securityId }}</strong><span v-if="row.name" class="position-name">{{ row.name }}</span></a></template></el-table-column><el-table-column prop="remainingQty" label="持股数" /><el-table-column prop="averageCost" label="加权单位成本" /><el-table-column label="最新价"><template #default="{ row }"><QuoteBadge :quote="quotes.bySecurity(row.securityId)" /></template></el-table-column><el-table-column prop="unrealizedPnl" label="浮盈额" /><el-table-column prop="strategyBreakevenPrice" label="策略回本参考价" /></el-table></el-card>
  <el-card class="panel"><template #header><div class="panel-title">自选股行情</div></template><div class="quote-grid"><div v-for="quote in quotes.quotes" :key="quote.symbol" class="quote-card"><div class="quote-symbol">{{ quote.symbol }}</div><div class="quote-price">{{ quote.lastPrice ?? '—' }}</div><div :class="['quote-change', Number(quote.changePct ?? 0) >= 0 ? 'up' : 'down']">{{ quote.changePct ? `${Number(quote.changePct) >= 0 ? '+' : ''}${quote.changePct}%` : '—' }}</div><div class="muted">{{ quote.sourceTime ? new Date(quote.sourceTime).toLocaleString() : '无数据' }} <span v-if="quote.isStale" class="stale">· 已过期</span></div></div><div v-if="!quotes.quotes.length" class="empty">暂无自选股行情</div></div></el-card>
  <BuyTradeDialog v-if="selectedSecurity" v-model="buyVisible" :security-id="selectedSecurity.id" :account-id="selectedAccountId" :currency="selectedSecurity.currency" @saved="reloadQuickTrade" /><SellMatchDrawer v-if="selectedSecurity" v-model="sellVisible" :security-id="selectedSecurity.id" :account-id="selectedAccountId" :currency="selectedSecurity.currency" :lots="selectedLots" @saved="reloadQuickTrade" />
</template>
