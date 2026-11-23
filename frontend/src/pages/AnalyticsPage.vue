<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import type { AnalyticsSummary, Currency, Security } from '../api/types'
import { useAccountsStore } from '../stores/accounts'
import { formatDateTime, formatMoney, trendClass } from '../utils/format'
import { useViewport } from '../utils/viewport'

interface CycleItem {
  id?: string
  securityId?: string
  strategyId?: string
  status?: string
  openedAt?: string
  closedAt?: string | null
}

const { isMobile } = useViewport()
const accountsStore = useAccountsStore()
const summary = ref<AnalyticsSummary | null>(null)
const cycles = ref<CycleItem[]>([])
const securities = ref<Security[]>([])
const loading = ref(false)
const securityMap = computed(() => new Map(securities.value.map((item) => [item.id, item])))
const currency = computed<Currency | null>(() => summary.value?.currency ?? accountsStore.activeAccount?.currency ?? null)

/** 将周期内的证券 ID 映射为可读代码与名称。 */
function cycleSecurityLabel(securityId?: string): string {
  if (!securityId) return '—'
  const sec = securityMap.value.get(securityId)
  return sec ? `${sec.market}:${sec.symbol}${sec.name ? ` ${sec.name}` : ''}` : securityId
}

/** 按当前选中账户拉取复盘汇总与交易周期。 */
async function load(): Promise<void> {
  loading.value = true
  try {
    const accountId = accountsStore.activeAccountId || undefined
    const [summaryRes, cyclesRes, secRes] = await Promise.allSettled([
      api.summary(accountId),
      api.cycles(undefined, accountId),
      api.securities(),
    ])
    if (summaryRes.status === 'fulfilled') summary.value = summaryRes.value
    if (cyclesRes.status === 'fulfilled') cycles.value = cyclesRes.value as CycleItem[]
    if (secRes.status === 'fulfilled') securities.value = secRes.value
  } finally {
    loading.value = false
  }
}

watch(() => accountsStore.activeAccountId, () => { void load() })
onMounted(() => { void accountsStore.loadAccounts(); void load() })
</script>
<template>
  <template v-if="!isMobile">
    <section class="page-head"><div><h2>复盘分析</h2><p class="muted">按事实流水汇总资金与收益（当前币种：{{ currency ?? '—' }}）。</p></div></section>
    <div class="metric-grid" v-loading="loading">
      <el-card class="metric-card"><span class="metric-label">累计买入</span><div class="metric-value">{{ formatMoney(summary?.totalBuyCashOut, currency) }}</div></el-card>
      <el-card class="metric-card"><span class="metric-label">累计卖出</span><div class="metric-value">{{ formatMoney(summary?.totalSellCashIn, currency) }}</div></el-card>
      <el-card class="metric-card"><span class="metric-label">已实现收益</span><div :class="['metric-value', trendClass(summary?.realizedPnl)]">{{ formatMoney(summary?.realizedPnl, currency) }}</div></el-card>
      <el-card class="metric-card"><span class="metric-label">剩余成本</span><div class="metric-value">{{ formatMoney(summary?.remainingCost, currency) }}</div></el-card>
    </div>
    <el-card class="panel"><template #header><div class="panel-title">交易周期 <span class="muted">{{ cycles.length }} 个周期</span></div></template><el-empty v-if="!cycles.length" description="暂无已归档周期" /><el-table v-else :data="cycles"><el-table-column label="证券" min-width="180"><template #default="{ row }">{{ cycleSecurityLabel(row.securityId) }}</template></el-table-column><el-table-column prop="strategyId" label="策略" /><el-table-column prop="status" label="状态" /><el-table-column label="开始时间" min-width="170"><template #default="{ row }">{{ formatDateTime(row.openedAt) }}</template></el-table-column><el-table-column label="结束时间" min-width="170"><template #default="{ row }">{{ row.closedAt ? formatDateTime(row.closedAt) : '进行中' }}</template></el-table-column></el-table></el-card>
  </template>
  <template v-else>
    <section class="m-section m-analytics-grid" aria-label="复盘指标" v-loading="loading">
      <div class="m-analytics-cell"><span class="m-analytics-label">累计买入</span><strong class="m-analytics-val num">{{ formatMoney(summary?.totalBuyCashOut, currency) }}</strong></div>
      <div class="m-analytics-cell"><span class="m-analytics-label">累计卖出</span><strong class="m-analytics-val num">{{ formatMoney(summary?.totalSellCashIn, currency) }}</strong></div>
      <div class="m-analytics-cell"><span class="m-analytics-label">已实现收益</span><strong :class="['m-analytics-val', 'num', trendClass(summary?.realizedPnl)]">{{ formatMoney(summary?.realizedPnl, currency) }}</strong></div>
      <div class="m-analytics-cell"><span class="m-analytics-label">剩余成本</span><strong class="m-analytics-val num">{{ formatMoney(summary?.remainingCost, currency) }}</strong></div>
    </section>
    <section class="m-section">
      <div class="m-section-head"><span>交易周期</span><span class="m-section-sub">{{ cycles.length }} 个周期</span></div>
      <div v-if="cycles.length" class="m-cycle-list">
        <article v-for="(cycle, index) in cycles" :key="cycle.id ?? `${cycle.securityId ?? ''}-${index}`" class="m-cycle-item">
          <div class="m-cycle-top">
            <strong>{{ cycleSecurityLabel(cycle.securityId) }}</strong>
            <span class="m-cycle-status">{{ cycle.status ?? '—' }}</span>
          </div>
          <div class="m-cycle-meta">策略 {{ cycle.strategyId ?? '—' }}</div>
          <div class="m-cycle-time num">起 {{ formatDateTime(cycle.openedAt) }} · 止 {{ cycle.closedAt ? formatDateTime(cycle.closedAt) : '进行中' }}</div>
        </article>
      </div>
      <div v-else class="m-empty">暂无已归档周期</div>
    </section>
  </template>
</template>
<style scoped>
.m-analytics-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.m-analytics-cell { padding: 9px 10px; display: flex; flex-direction: column; gap: 3px; border-right: 1px solid #edf0f5; border-bottom: 1px solid #edf0f5; min-width: 0; color: #182230; }
.m-analytics-cell:nth-child(even) { border-right: 0; }
.m-analytics-cell:nth-last-child(-n + 2) { border-bottom: 0; }
.m-analytics-label { font-size: 11px; color: #7d899b; }
.m-analytics-val { font-size: 15px; font-weight: 700; overflow-wrap: anywhere; }
.m-cycle-list { display: flex; flex-direction: column; }
.m-cycle-item { padding: 8px 10px; border-bottom: 1px solid #edf0f5; font-size: 12px; }
.m-cycle-item:last-child { border-bottom: 0; }
.m-cycle-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.m-cycle-status { font-size: 11px; color: #64748b; }
.m-cycle-meta, .m-cycle-time { margin-top: 3px; font-size: 11px; color: #7d899b; overflow-wrap: anywhere; }
</style>
