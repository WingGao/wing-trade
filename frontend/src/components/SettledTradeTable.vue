<script setup lang="ts">
import { computed } from 'vue'
import type { Currency, SettledTradeDetail } from '../api/types'
import { formatDateTime, formatMoney, formatQuantity, trendClass } from '../utils/format'
import { useViewport } from '../utils/viewport'

const props = defineProps<{ details: SettledTradeDetail[]; currency: Currency }>()
const { isMobile } = useViewport()

/** 仅排序服务端已结清成交投影，不重新计算数量、现金或盈亏。 */
const sortedDetails = computed(() => [...props.details].sort((a, b) => new Date(b.executedAt).getTime() - new Date(a.executedAt).getTime() || b.tradeId.localeCompare(a.tradeId) || b.lotId.localeCompare(a.lotId)))

/** 同一卖出可能跨多个批次，组合事实编号保持每个明细行独立。 */
function detailKey(detail: SettledTradeDetail): string {
  return `${detail.lotId}:${detail.tradeId}:${detail.side}`
}
</script>

<template>
  <el-table v-if="!isMobile" :data="sortedDetails" :row-key="detailKey" stripe empty-text="暂无已结清买卖明细" class="settled-trade-table">
    <el-table-column label="批次 ID" min-width="100"><template #default="{ row }"><span class="lot-code" :title="row.lotId">{{ row.lotId.slice(0, 8) }}</span></template></el-table-column>
    <el-table-column label="方向" min-width="78"><template #default="{ row }"><el-tag :type="row.side === 'BUY' ? 'danger' : 'success'" :title="`交易 ID：${row.tradeId}`">{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag></template></el-table-column>
    <el-table-column label="成交时间" min-width="170"><template #default="{ row }">{{ formatDateTime(row.executedAt) }}</template></el-table-column>
    <el-table-column label="原始成交价" min-width="120"><template #default="{ row }">{{ formatMoney(row.price, currency, 3) }}</template></el-table-column>
    <el-table-column label="本批数量" min-width="150"><template #default="{ row }"><strong>{{ formatQuantity(row.qty) }}</strong><div v-if="row.side === 'SELL'" class="muted">整笔成交 {{ formatQuantity(row.originalTradeQty) }} 股</div></template></el-table-column>
    <el-table-column label="含费买入 / 卖出净回款" min-width="185"><template #default="{ row }">{{ formatMoney(row.cashAmount, currency) }}<div class="muted">{{ row.side === 'BUY' ? '含费买入金额' : '本批分摊净回款' }}</div></template></el-table-column>
    <el-table-column label="已实现盈亏" min-width="130"><template #default="{ row }"><span v-if="row.side === 'SELL'" :class="trendClass(row.realizedPnl)">{{ formatMoney(row.realizedPnl, currency) }}</span><span v-else class="muted">—</span></template></el-table-column>
  </el-table>
  <div v-else class="m-settled-list">
    <div v-if="!sortedDetails.length" class="m-empty">暂无已结清买卖明细</div>
    <article v-for="row in sortedDetails" :key="detailKey(row)" class="m-settled-item">
      <div class="m-settled-head">
        <el-tag :type="row.side === 'BUY' ? 'danger' : 'success'" size="small" :title="`交易 ID：${row.tradeId}`">{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag>
        <span class="lot-code" :title="row.lotId">{{ row.lotId.slice(0, 8) }}</span>
        <span class="m-settled-time num">{{ formatDateTime(row.executedAt) }}</span>
      </div>
      <div class="m-settled-grid">
        <div class="m-settled-cell">
          <span class="m-settled-label">成交价 / 本批</span>
          <strong class="m-settled-val num">{{ formatMoney(row.price, currency, 3) }} × {{ formatQuantity(row.qty) }}</strong>
          <span v-if="row.side === 'SELL'" class="m-settled-sub num">整笔 {{ formatQuantity(row.originalTradeQty) }} 股</span>
        </div>
        <div class="m-settled-cell">
          <span class="m-settled-label">{{ row.side === 'BUY' ? '含费买入' : '分摊净回款' }}</span>
          <strong class="m-settled-val num">{{ formatMoney(row.cashAmount, currency) }}</strong>
        </div>
        <div class="m-settled-cell m-settled-right">
          <span class="m-settled-label">已实现盈亏</span>
          <strong v-if="row.side === 'SELL'" :class="['m-settled-val', 'num', trendClass(row.realizedPnl)]">{{ formatMoney(row.realizedPnl, currency) }}</strong>
          <span v-else class="m-settled-sub">—</span>
        </div>
      </div>
    </article>
  </div>
</template>

<style scoped>
.lot-code { border-bottom: 1px dotted currentColor; cursor: help; font-family: ui-monospace, monospace; }
.m-settled-list { display: flex; flex-direction: column; }
.m-settled-item { padding: 8px 10px; border-bottom: 1px solid #edf0f5; }
.m-settled-item:last-child { border-bottom: 0; }
.m-settled-head { display: flex; align-items: center; gap: 6px; margin-bottom: 5px; font-size: 11px; flex-wrap: wrap; }
.m-settled-time { color: #7d899b; margin-left: auto; }
.m-settled-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr) minmax(0, 0.9fr); gap: 6px; }
.m-settled-cell { display: flex; flex-direction: column; gap: 2px; min-width: 0; color: #182230; }
.m-settled-right { text-align: right; }
.m-settled-label { font-size: 10px; color: #7d899b; }
.m-settled-val { font-size: 12px; font-weight: 700; overflow-wrap: anywhere; }
.m-settled-sub { font-size: 10px; color: #7d899b; }
</style>
