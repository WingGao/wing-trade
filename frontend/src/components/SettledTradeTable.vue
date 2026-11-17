<script setup lang="ts">
import { computed } from 'vue'
import type { Currency, SettledTradeDetail } from '../api/types'
import { formatDateTime, formatMoney, formatQuantity, trendClass } from '../utils/format'

const props = defineProps<{ details: SettledTradeDetail[]; currency: Currency }>()

/** 仅排序服务端已结清成交投影，不重新计算数量、现金或盈亏。 */
const sortedDetails = computed(() => [...props.details].sort((a, b) => new Date(b.executedAt).getTime() - new Date(a.executedAt).getTime() || b.tradeId.localeCompare(a.tradeId) || b.lotId.localeCompare(a.lotId)))

/** 同一卖出可能跨多个批次，组合事实编号保持每个明细行独立。 */
function detailKey(detail: SettledTradeDetail): string {
  return `${detail.lotId}:${detail.tradeId}:${detail.side}`
}
</script>

<template>
  <el-table :data="sortedDetails" :row-key="detailKey" stripe empty-text="暂无已结清买卖明细" class="settled-trade-table">
    <el-table-column label="批次 ID" min-width="100"><template #default="{ row }"><span class="lot-code" :title="row.lotId">{{ row.lotId.slice(0, 8) }}</span></template></el-table-column>
    <el-table-column label="方向" min-width="78"><template #default="{ row }"><el-tag :type="row.side === 'BUY' ? 'danger' : 'success'" :title="`交易 ID：${row.tradeId}`">{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag></template></el-table-column>
    <el-table-column label="成交时间" min-width="170"><template #default="{ row }">{{ formatDateTime(row.executedAt) }}</template></el-table-column>
    <el-table-column label="原始成交价" min-width="120"><template #default="{ row }">{{ formatMoney(row.price, currency, 3) }}</template></el-table-column>
    <el-table-column label="本批数量" min-width="150"><template #default="{ row }"><strong>{{ formatQuantity(row.qty) }}</strong><div v-if="row.side === 'SELL'" class="muted">整笔成交 {{ formatQuantity(row.originalTradeQty) }} 股</div></template></el-table-column>
    <el-table-column label="含费买入 / 卖出净回款" min-width="185"><template #default="{ row }">{{ formatMoney(row.cashAmount, currency) }}<div class="muted">{{ row.side === 'BUY' ? '含费买入金额' : '本批分摊净回款' }}</div></template></el-table-column>
    <el-table-column label="已实现盈亏" min-width="130"><template #default="{ row }"><span v-if="row.side === 'SELL'" :class="trendClass(row.realizedPnl)">{{ formatMoney(row.realizedPnl, currency) }}</span><span v-else class="muted">—</span></template></el-table-column>
  </el-table>
</template>

<style scoped>
.lot-code { border-bottom: 1px dotted currentColor; cursor: help; font-family: ui-monospace, monospace; }
</style>
