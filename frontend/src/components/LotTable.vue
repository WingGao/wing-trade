<script setup lang="ts">
import { computed } from 'vue'
import type { Currency, Lot } from '../api/types'
import { formatDateTime, formatMoney, formatPercent, formatQuantity, formatTargetPercent, trendClass } from '../utils/format'

const props = defineProps<{ lots: Lot[]; includeClosed: boolean; currency?: Currency | null }>()
const emit = defineEmits<{ setTarget: [lot: Lot] }>()
const sortedLots = computed(() => [...props.lots].sort((a, b) => new Date(b.executedAt).getTime() - new Date(a.executedAt).getTime() || b.id.localeCompare(a.id)))

/** 展示仍可操作的分段目标，已完成和取消记录不再掩盖上级继承。 */
function formatTargets(lot: Lot): string {
  return (lot.targets ?? []).filter((target) => !['CANCELLED', 'COMPLETED'].includes(target.status))
    .map((target) => `${formatMoney(target.targetPriceEstimate, props.currency, 3)} / ${formatTargetPercent(target.targetReturnPct)} · ${formatQuantity(target.plannedSellQty)} 股${target.status === 'TRIGGERED' ? '（已触发）' : target.status === 'CONFLICT' ? '（计划量冲突）' : ''}`).join(' · ')
}
</script>
<template>
  <el-table :data="sortedLots" row-key="id" stripe empty-text="暂无批次" class="lot-table">
    <el-table-column label="批次 ID" min-width="110"><template #default="{ row }"><span class="lot-code" :title="row.id">{{ row.id.slice(0, 8) }}</span></template></el-table-column>
    <el-table-column label="买入日期" min-width="170"><template #default="{ row }">{{ formatDateTime(row.executedAt) }}</template></el-table-column>
    <el-table-column label="初始股数" min-width="95"><template #default="{ row }">{{ formatQuantity(row.originalQty) }}</template></el-table-column>
    <el-table-column label="剩余股数" min-width="95"><template #default="{ row }">{{ formatQuantity(row.remainingQty) }}</template></el-table-column>
    <el-table-column label="含费单位成本" min-width="120"><template #default="{ row }">{{ formatMoney(row.unitCost, currency, 3) }}</template></el-table-column>
    <el-table-column label="状态"><template #default="{ row }"><el-tag :type="row.status === 'OPEN' ? 'success' : 'info'">{{ row.status === 'OPEN' ? '持有中' : '已结清' }}</el-tag></template></el-table-column>
    <el-table-column label="浮盈 / 收益率" min-width="160"><template #default="{ row }"><template v-if="row.status === 'OPEN'"><div :class="trendClass(row.unrealizedPnl)">{{ formatMoney(row.unrealizedPnl, currency) }}</div><div :class="['muted', trendClass(row.unrealizedReturnPct)]">{{ formatPercent(row.unrealizedReturnPct) }}</div></template><span v-else class="muted">—</span></template></el-table-column>
    <el-table-column label="止盈目标" min-width="235"><template #default="{ row }"><div v-if="formatTargets(row)">{{ formatTargets(row) }}<div class="muted">批次设置</div></div><template v-if="row.effectiveTarget && row.effectiveTarget.source !== 'LOT'"><div>{{ formatMoney(row.effectiveTarget.targetPriceEstimate, currency, 3) }} / {{ formatTargetPercent(row.effectiveTarget.targetReturnPct) }}</div><div class="muted">继承{{ row.effectiveTarget.source === 'SECURITY' ? '个股' : '全局' }}</div></template><span v-else-if="!formatTargets(row)" class="muted">未设置</span><el-button v-if="row.status === 'OPEN'" link type="primary" @click="emit('setTarget', row)">设置</el-button></template></el-table-column>
  </el-table>
</template>
<style scoped>
.lot-code { border-bottom: 1px dotted currentColor; cursor: help; font-family: ui-monospace, monospace; }
</style>
