<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useLedgerStore } from '../stores/ledger'
import { useQuotesStore } from '../stores/quotes'
import { usePreferencesStore } from '../stores/preferences'
import { api } from '../api/client'
import QuoteBadge from '../components/QuoteBadge.vue'
const ledger = useLedgerStore(); const quotes = useQuotesStore(); const preferences = usePreferencesStore(); const router = useRouter()
const positions = computed(() => ledger.positions)
onMounted(async () => { await ledger.loadDashboard(); await quotes.load() })
async function refresh() { await quotes.refresh(); ElMessage.success('行情刷新请求已完成') }
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
  <div class="metric-grid"><el-card class="metric-card"><span class="metric-label">累计买入现金流</span><div class="metric-value">¥ {{ ledger.summary?.totalBuyCashOut ?? '—' }}</div></el-card><el-card class="metric-card"><span class="metric-label">累计卖出回款</span><div class="metric-value">¥ {{ ledger.summary?.totalSellCashIn ?? '—' }}</div></el-card><el-card class="metric-card"><span class="metric-label">已实现收益</span><div class="metric-value positive">¥ {{ ledger.summary?.realizedPnl ?? '—' }}</div></el-card><el-card class="metric-card"><span class="metric-label">未实现毛浮盈（参考）</span><div class="metric-value" :class="Number(ledger.summary?.unrealizedPnl ?? 0) >= 0 ? 'positive' : 'negative'">¥ {{ ledger.summary?.unrealizedPnl ?? '行情不可用' }}</div></el-card></div>
  <el-card class="panel"><template #header><div class="panel-title">活动仓位</div></template><el-table :data="positions" v-loading="ledger.loading" stripe empty-text="暂无持仓"><el-table-column label="证券" min-width="180"><template #default="{ row }"><a class="security-link" href="#" @click.prevent="openPosition(row)">{{ row.securityId }}</a></template></el-table-column><el-table-column prop="remainingQty" label="持股数" /><el-table-column prop="averageCost" label="加权单位成本" /><el-table-column label="最新价"><template #default="{ row }"><QuoteBadge :quote="quotes.bySecurity(row.securityId)" /></template></el-table-column><el-table-column prop="unrealizedPnl" label="浮盈额" /><el-table-column prop="strategyBreakevenPrice" label="策略回本参考价" /></el-table></el-card>
  <el-card class="panel"><template #header><div class="panel-title">自选股行情</div></template><div class="quote-grid"><div v-for="quote in quotes.quotes" :key="quote.symbol" class="quote-card"><div class="quote-symbol">{{ quote.symbol }}</div><div class="quote-price">{{ quote.lastPrice ?? '—' }}</div><div :class="['quote-change', Number(quote.changePct ?? 0) >= 0 ? 'up' : 'down']">{{ quote.changePct ? `${Number(quote.changePct) >= 0 ? '+' : ''}${quote.changePct}%` : '—' }}</div><div class="muted">{{ quote.sourceTime ? new Date(quote.sourceTime).toLocaleString() : '无数据' }} <span v-if="quote.isStale" class="stale">· 已过期</span></div></div><div v-if="!quotes.quotes.length" class="empty">暂无自选股行情</div></div></el-card>
</template>
