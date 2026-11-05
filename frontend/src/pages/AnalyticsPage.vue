<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { AnalyticsSummary } from '../api/types'
const summary = ref<AnalyticsSummary | null>(null); const cycles = ref<unknown[]>([])
onMounted(async () => { summary.value = await api.summary(); cycles.value = await api.cycles() })
</script>
<template><section class="page-head"><div><h2>复盘分析</h2><p class="muted">按事实流水汇总资金与收益。</p></div></section><div class="metric-grid"><el-card class="metric-card"><span class="metric-label">累计买入</span><div class="metric-value">¥{{ summary?.totalBuyCashOut ?? '—' }}</div></el-card><el-card class="metric-card"><span class="metric-label">累计卖出</span><div class="metric-value">¥{{ summary?.totalSellCashIn ?? '—' }}</div></el-card><el-card class="metric-card"><span class="metric-label">已实现收益</span><div class="metric-value positive">¥{{ summary?.realizedPnl ?? '—' }}</div></el-card><el-card class="metric-card"><span class="metric-label">剩余成本</span><div class="metric-value">¥{{ summary?.remainingCost ?? '—' }}</div></el-card></div><el-card class="panel"><template #header><div class="panel-title">交易周期 <span class="muted">{{ cycles.length }} 个周期</span></div></template><el-empty v-if="!cycles.length" description="暂无已归档周期" /><el-table v-else :data="cycles"><el-table-column prop="securityId" label="证券" /><el-table-column prop="strategyId" label="策略" /><el-table-column prop="status" label="状态" /><el-table-column prop="openedAt" label="开始时间" /><el-table-column prop="closedAt" label="结束时间" /></el-table></el-card></template>
