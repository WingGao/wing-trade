<script setup lang="ts">
import { computed } from 'vue'
import type { Currency, Lot, Quote } from '../api/types'
import { formatDateTime, formatMoney, formatPercent, formatQuantity, formatTargetPercent, priceDigits, quantityUnit, trendClass } from '../utils/format'
import { useViewport } from '../utils/viewport'

const props = defineProps<{ lots: Lot[]; includeClosed: boolean; currency?: Currency | null; isFund?: boolean; quote?: Quote | null }>()
const emit = defineEmits<{ setTarget: [lot: Lot] }>()
const { isMobile } = useViewport()
const sortedLots = computed(() => [...props.lots].sort((a, b) => new Date(b.executedAt).getTime() - new Date(a.executedAt).getTime() || b.id.localeCompare(a.id)))

/** 返回批次当前成本总价：持有中展示剩余成本，已结清展示初始总成本。 */
function lotCostTotal(lot: Lot): string {
  return lot.status === 'OPEN' ? lot.remainingCost : lot.originalCost
}

/** 计算持有中批次的展示用当前总价值（剩余数量 × 最新价），缺行情时返回空值。 */
function lotMarketValue(lot: Lot): string | null {
  if (lot.status !== 'OPEN') return null
  const price = props.quote?.lastPrice
  if (price === null || price === undefined || price === '') return null
  const qty = Number(lot.remainingQty)
  const latest = Number(price)
  if (!Number.isFinite(qty) || !Number.isFinite(latest) || latest <= 0) return null
  const total = qty * latest
  return Number.isFinite(total) ? total.toFixed(2) : null
}

/** 展示仍可操作的分段目标，已完成和取消记录不再掩盖上级继承。 */
function formatTargets(lot: Lot): string {
  const digits = priceDigits(props.isFund, 3)
  const unit = quantityUnit(props.isFund)
  return (lot.targets ?? []).filter((target) => !['CANCELLED', 'COMPLETED'].includes(target.status))
    .map((target) => `${formatMoney(target.targetPriceEstimate, props.currency, digits)} / ${formatTargetPercent(target.targetReturnPct)} · ${formatQuantity(target.plannedSellQty, props.isFund)} ${unit}${target.status === 'TRIGGERED' ? '（已触发）' : target.status === 'CONFLICT' ? '（计划量冲突）' : ''}`).join(' · ')
}
</script>
<template>
  <el-table v-if="!isMobile" :data="sortedLots" row-key="id" stripe empty-text="暂无批次" class="lot-table">
    <el-table-column label="批次 ID" min-width="110"><template #default="{ row }"><span class="lot-code" :title="row.id">{{ row.id.slice(0, 8) }}</span></template></el-table-column>
    <el-table-column label="买入日期" min-width="170"><template #default="{ row }">{{ formatDateTime(row.executedAt) }}</template></el-table-column>
    <el-table-column :label="isFund ? '初始份额' : '初始股数'" min-width="95"><template #default="{ row }">{{ formatQuantity(row.originalQty, isFund) }}</template></el-table-column>
    <el-table-column :label="isFund ? '剩余份额' : '剩余股数'" min-width="95"><template #default="{ row }">{{ formatQuantity(row.remainingQty, isFund) }}</template></el-table-column>
    <el-table-column label="含费单位成本" min-width="120"><template #default="{ row }">{{ formatMoney(row.unitCost, currency, priceDigits(isFund, 3)) }}</template></el-table-column>
    <el-table-column label="成本总价" min-width="125"><template #default="{ row }"><div>{{ formatMoney(lotCostTotal(row), currency) }}</div><div v-if="row.status === 'OPEN' && row.remainingCost !== row.originalCost" class="muted">初始 {{ formatMoney(row.originalCost, currency) }}</div></template></el-table-column>
    <el-table-column :label="isFund ? '现值 / 净值' : '现值 / 现价'" min-width="135"><template #default="{ row }"><template v-if="row.status === 'OPEN' && lotMarketValue(row) !== null"><div>{{ formatMoney(lotMarketValue(row), currency) }}</div><div class="muted">{{ isFund ? '净值' : '现价' }} {{ formatMoney(quote?.lastPrice, currency, priceDigits(isFund, 3)) }}</div></template><span v-else class="muted">—</span></template></el-table-column>
    <el-table-column label="状态"><template #default="{ row }"><el-tag :type="row.status === 'OPEN' ? 'success' : 'info'">{{ row.status === 'OPEN' ? '持有中' : '已结清' }}</el-tag></template></el-table-column>
    <el-table-column label="浮盈 / 收益率" min-width="160"><template #default="{ row }"><template v-if="row.status === 'OPEN'"><div :class="trendClass(row.unrealizedPnl)">{{ formatMoney(row.unrealizedPnl, currency) }}</div><div :class="['muted', trendClass(row.unrealizedReturnPct)]">{{ formatPercent(row.unrealizedReturnPct) }}</div></template><span v-else class="muted">—</span></template></el-table-column>
    <el-table-column label="止盈目标" min-width="235"><template #default="{ row }"><div v-if="formatTargets(row)">{{ formatTargets(row) }}<div class="muted">批次设置</div></div><template v-if="row.effectiveTarget && row.effectiveTarget.source !== 'LOT'"><div>{{ formatMoney(row.effectiveTarget.targetPriceEstimate, currency, priceDigits(isFund, 3)) }} / {{ formatTargetPercent(row.effectiveTarget.targetReturnPct) }}</div><div class="muted">继承{{ row.effectiveTarget.source === 'SECURITY' ? '个股' : '全局' }}</div></template><span v-else-if="!formatTargets(row)" class="muted">未设置</span><el-button v-if="row.status === 'OPEN'" link type="primary" @click="emit('setTarget', row)">设置</el-button></template></el-table-column>
  </el-table>
  <div v-else class="m-lot-list">
    <div v-if="!sortedLots.length" class="m-empty">暂无批次</div>
    <article v-for="row in sortedLots" :key="row.id" class="m-lot-item">
      <div class="m-lot-head">
        <div class="m-lot-meta">
          <span class="lot-code" :title="row.id">{{ row.id.slice(0, 8) }}</span>
          <el-tag :type="row.status === 'OPEN' ? 'success' : 'info'" size="small">{{ row.status === 'OPEN' ? '持有中' : '已结清' }}</el-tag>
          <span class="m-lot-time num">{{ formatDateTime(row.executedAt) }}</span>
        </div>
        <el-button v-if="row.status === 'OPEN'" link type="primary" size="small" @click="emit('setTarget', row)">设置</el-button>
      </div>
      <div class="m-lot-grid">
        <div class="m-lot-cell">
          <span class="m-lot-label">剩余 / 初始</span>
          <strong class="m-lot-val num">{{ formatQuantity(row.remainingQty, isFund) }} / {{ formatQuantity(row.originalQty, isFund) }}</strong>
          <span class="m-lot-sub num">单价 {{ formatMoney(row.unitCost, currency, priceDigits(isFund, 3)) }}</span>
        </div>
        <div class="m-lot-cell">
          <span class="m-lot-label">成本总价 / 现值</span>
          <strong class="m-lot-val num">{{ formatMoney(lotCostTotal(row), currency) }}</strong>
          <span v-if="row.status === 'OPEN' && lotMarketValue(row) !== null" class="m-lot-sub num">现 {{ formatMoney(lotMarketValue(row), currency) }} ({{ formatMoney(quote?.lastPrice, currency, priceDigits(isFund, 3)) }})</span>
          <span v-else class="m-lot-sub">—</span>
        </div>
        <div class="m-lot-cell m-lot-cell-right">
          <span class="m-lot-label">浮盈 / 收益率</span>
          <template v-if="row.status === 'OPEN'">
            <strong :class="['m-lot-val', 'num', trendClass(row.unrealizedPnl)]">{{ formatMoney(row.unrealizedPnl, currency) }}</strong>
            <span :class="['m-lot-sub', 'num', trendClass(row.unrealizedReturnPct)]">{{ formatPercent(row.unrealizedReturnPct) }}</span>
          </template>
          <span v-else class="m-lot-sub">—</span>
        </div>
      </div>
      <div class="m-lot-target">
        <span class="m-lot-label">止盈：</span>
        <template v-if="formatTargets(row)">
          <span class="num">{{ formatTargets(row) }}</span>
          <span class="m-lot-source">（批次设置）</span>
        </template>
        <template v-if="row.effectiveTarget && row.effectiveTarget.source !== 'LOT'">
          <span class="num">{{ formatMoney(row.effectiveTarget.targetPriceEstimate, currency, priceDigits(isFund, 3)) }} / {{ formatTargetPercent(row.effectiveTarget.targetReturnPct) }}</span>
          <span class="m-lot-source">（继承{{ row.effectiveTarget.source === 'SECURITY' ? '个股' : '全局' }}）</span>
        </template>
        <span v-else-if="!formatTargets(row)" class="m-lot-source">未设置</span>
      </div>
    </article>
  </div>
</template>
<style scoped>
.lot-code { border-bottom: 1px dotted currentColor; cursor: help; font-family: ui-monospace, monospace; }
.m-lot-list { display: flex; flex-direction: column; }
.m-lot-item { padding: 8px 10px; border-bottom: 1px solid #edf0f5; }
.m-lot-item:last-child { border-bottom: 0; }
.m-lot-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 6px; }
.m-lot-meta { display: flex; align-items: center; gap: 6px; min-width: 0; flex-wrap: wrap; font-size: 11px; }
.m-lot-time { color: #7d899b; }
.m-lot-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
.m-lot-cell { display: flex; flex-direction: column; gap: 2px; min-width: 0; color: #182230; }
.m-lot-cell-right { text-align: right; }
.m-lot-label { font-size: 10px; color: #7d899b; }
.m-lot-val { font-size: 12px; font-weight: 700; overflow-wrap: anywhere; }
.m-lot-sub { font-size: 11px; }
.m-lot-sub:not(.up):not(.down):not(.neutral) { color: #7d899b; }
.m-lot-target { margin-top: 6px; padding-top: 5px; border-top: 1px dashed #edf0f5; font-size: 11px; color: #334155; line-height: 1.4; }
.m-lot-source { color: #7d899b; }
</style>
