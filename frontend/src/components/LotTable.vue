<script setup lang="ts">
import { computed } from 'vue'
import type { Currency, Lot, Quote } from '../api/types'
import { formatDateTime, formatMoney, formatPercent, formatQuantity, formatTargetPercent, priceDigits, quantityUnit, trendClass } from '../utils/format'
import { useViewport } from '../utils/viewport'

const props = defineProps<{ lots: Lot[]; includeClosed: boolean; currency?: Currency | null; isFund?: boolean; quote?: Quote | null }>()
const emit = defineEmits<{ setTarget: [lot: Lot]; sell: [lot: Lot] }>()
const { isMobile } = useViewport()
const qtyTarget = computed(() => ({ currency: props.currency, securityType: props.isFund ? 'FUND' : 'STOCK' }))
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

/** 判断持有中批次在盘中估算净值下是否已预估触达有效止盈目标价（不落库固化）。 */
function isEstimatedTargetReached(lot: Lot): boolean {
  if (lot.status !== 'OPEN' || !props.quote?.isEstimated || !props.quote.lastPrice) return false
  const current = Number(props.quote.lastPrice)
  if (!Number.isFinite(current) || current <= 0) return false
  const activeTargets = (lot.targets ?? []).filter((target) => target.status === 'ACTIVE' && target.alertEnabled && target.targetPriceEstimate)
  if (activeTargets.some((target) => current >= Number(target.targetPriceEstimate))) return true
  const inherited = lot.effectiveTarget
  if (inherited && inherited.source !== 'LOT' && inherited.alertEnabled && inherited.targetPriceEstimate) {
    return current >= Number(inherited.targetPriceEstimate)
  }
  return false
}

/** 展示仍可操作的分段目标，已完成和取消记录不再掩盖上级继承。 */
function formatTargets(lot: Lot): string {
  const digits = priceDigits(props.isFund, 3)
  const unit = quantityUnit(props.isFund)
  return (lot.targets ?? []).filter((target) => !['CANCELLED', 'COMPLETED'].includes(target.status))
    .map((target) => `${formatMoney(target.targetPriceEstimate, props.currency, digits)} / ${formatTargetPercent(target.targetReturnPct)} · ${formatQuantity(target.plannedSellQty, qtyTarget.value)} ${unit}${target.status === 'TRIGGERED' ? '（已触发）' : target.status === 'CONFLICT' ? '（计划量冲突）' : ''}`).join(' · ')
}
</script>
<template>
  <el-table v-if="!isMobile" :data="sortedLots" row-key="id" stripe empty-text="暂无批次" class="lot-table">
    <el-table-column label="批次 ID" min-width="95"><template #default="{ row }"><span class="lot-code" :title="row.id">{{ row.id.slice(0, 8) }}</span></template></el-table-column>
    <el-table-column label="买入日期" min-width="155"><template #default="{ row }">{{ formatDateTime(row.executedAt) }}</template></el-table-column>
    <el-table-column :label="isFund ? '份额 / 单位成本' : '股数 / 单位成本'" min-width="160">
      <template #default="{ row }">
        <div class="lot-cell-lines">
          <div class="lot-primary num">
            剩 {{ formatQuantity(row.remainingQty, qtyTarget) }}
            <span class="muted">/ 初 {{ formatQuantity(row.originalQty, qtyTarget) }}</span>
          </div>
          <div class="lot-sub muted num">含费单价 {{ formatMoney(row.unitCost, currency, priceDigits(isFund, 3)) }}</div>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="成本 / 现值" min-width="195">
      <template #default="{ row }">
        <div class="lot-cell-lines">
          <div class="lot-primary num">
            成本 {{ formatMoney(lotCostTotal(row), currency) }}
            <span v-if="row.status === 'OPEN' && row.remainingCost !== row.originalCost" class="muted lot-inline-note">（初 {{ formatMoney(row.originalCost, currency) }}）</span>
          </div>
          <div v-if="row.status === 'OPEN' && lotMarketValue(row) !== null" class="lot-sub num">
            现值 {{ formatMoney(lotMarketValue(row), currency) }}
            <span class="muted">（{{ isFund ? '净值' : '现价' }} {{ formatMoney(quote?.lastPrice, currency, priceDigits(isFund, 3)) }}）</span>
          </div>
          <div v-else class="lot-sub muted">现值 —</div>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="状态" width="88"><template #default="{ row }"><el-tag :type="row.status === 'OPEN' ? 'success' : 'info'">{{ row.status === 'OPEN' ? '持有中' : '已结清' }}</el-tag></template></el-table-column>
    <el-table-column label="浮盈 / 止盈目标" min-width="240">
      <template #default="{ row }">
        <div class="lot-cell-lines">
          <div v-if="row.status === 'OPEN'" class="lot-pnl-line num">
            <span :class="['lot-primary', trendClass(row.unrealizedPnl)]">{{ formatMoney(row.unrealizedPnl, currency) }}</span>
            <span :class="['lot-pnl-pct', trendClass(row.unrealizedReturnPct)]">{{ formatPercent(row.unrealizedReturnPct) }}</span>
          </div>
          <div v-else class="lot-sub muted">浮盈 —</div>
          <div v-if="formatTargets(row)" class="lot-target-line">
            <span class="num">止盈 {{ formatTargets(row) }}</span>
            <span class="muted lot-inline-note">（批次设置）</span>
          </div>
          <div v-if="row.effectiveTarget && row.effectiveTarget.source !== 'LOT'" class="lot-target-line">
            <span class="num">止盈 {{ formatMoney(row.effectiveTarget.targetPriceEstimate, currency, priceDigits(isFund, 3)) }} / {{ formatTargetPercent(row.effectiveTarget.targetReturnPct) }}</span>
            <span class="muted lot-inline-note">（继承{{ row.effectiveTarget.source === 'SECURITY' ? '个股' : '全局' }}）</span>
          </div>
          <div v-else-if="!formatTargets(row)" class="lot-target-line muted">止盈 未设置</div>
          <el-tag v-if="isEstimatedTargetReached(row)" type="warning" size="small" class="lot-target-tag">预估达标</el-tag>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="操作" width="72">
      <template #default="{ row }">
        <div v-if="row.status === 'OPEN'" class="lot-actions">
          <el-button link type="danger" @click="emit('sell', row)">卖出</el-button>
          <el-button link type="primary" @click="emit('setTarget', row)">设置</el-button>
        </div>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
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
        <div v-if="row.status === 'OPEN'" class="m-lot-actions">
          <el-button link type="danger" size="small" @click="emit('sell', row)">卖出</el-button>
          <el-button link type="primary" size="small" @click="emit('setTarget', row)">设置</el-button>
        </div>
      </div>
      <div class="m-lot-grid">
        <div class="m-lot-cell">
          <span class="m-lot-label">剩余 / 初始</span>
          <strong class="m-lot-val num">{{ formatQuantity(row.remainingQty, qtyTarget) }} / {{ formatQuantity(row.originalQty, qtyTarget) }}</strong>
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
        <el-tag v-if="isEstimatedTargetReached(row)" type="warning" size="small" style="margin-left:6px">预估达标</el-tag>
      </div>
    </article>
  </div>
</template>
<style scoped>
.lot-code { border-bottom: 1px dotted currentColor; cursor: help; font-family: ui-monospace, monospace; }
.lot-cell-lines { display: flex; flex-direction: column; gap: 3px; line-height: 1.4; }
.lot-primary { font-weight: 600; }
.lot-primary:not(.up):not(.down):not(.neutral) { color: var(--el-text-color-primary); }
.lot-sub { font-size: 12px; }
.lot-inline-note { font-weight: 400; font-size: 12px; }
.lot-pnl-line { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.lot-pnl-pct { font-size: 12px; font-weight: 500; }
.lot-target-line { font-size: 12px; line-height: 1.4; }
.lot-target-tag { width: fit-content; }
.lot-actions { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.lot-actions :deep(.el-button + .el-button) { margin-left: 0; }
.m-lot-list { display: flex; flex-direction: column; }
.m-lot-item { padding: 8px 10px; border-bottom: 1px solid #edf0f5; }
.m-lot-item:last-child { border-bottom: 0; }
.m-lot-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 6px; }
.m-lot-actions { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; }
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
