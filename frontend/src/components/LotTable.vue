<script setup lang="ts">
import type { Lot } from '../api/types'
defineProps<{ lots: Lot[]; includeClosed: boolean }>()

function formatTargets(lot: Lot): string {
  return (lot.targets ?? [])
    .map((target) => `${target.targetPriceEstimate ?? '—'} (${target.status})`)
    .join(' · ')
}
</script>
<template>
  <el-table :data="lots" stripe empty-text="暂无批次">
    <el-table-column prop="executedAt" label="买入日期" min-width="150"><template #default="{ row }">{{ new Date(row.executedAt).toLocaleString() }}</template></el-table-column>
    <el-table-column prop="originalQty" label="初始股数" />
    <el-table-column prop="remainingQty" label="剩余股数" />
    <el-table-column prop="unitCost" label="含费单位成本" />
    <el-table-column prop="status" label="状态"><template #default="{ row }"><el-tag :type="row.status === 'OPEN' ? 'success' : 'info'">{{ row.status === 'OPEN' ? '持有中' : '已结清' }}</el-tag></template></el-table-column>
    <el-table-column label="止盈目标" min-width="190"><template #default="{ row }"><span v-if="row.targets?.length">{{ formatTargets(row) }}</span><span v-else class="muted">未设置</span></template></el-table-column>
  </el-table>
</template>
