<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TableInstance } from 'element-plus'
import type { Currency, Lot, Quote } from '../api/types'
import { formatDateTime, formatMoney, formatPercent, formatQuantity, formatTargetPercent, priceDigits, quantityUnit, trendClass } from '../utils/format'
import { buildLotDisplayRows, type LotDisplayRow } from '../utils/lotMerge'
import type { ManualSellMatchItem } from '../utils/manualSellMatch'
import { useViewport } from '../utils/viewport'

/** 批次行点击「卖出」时抛出的预选载荷（总量与对应批次明细）。 */
export interface LotSellPayload {
  qty: string
  matches: ManualSellMatchItem[]
}

const props = withDefaults(defineProps<{
  lots: Lot[]
  includeClosed: boolean
  mergeByPrice?: boolean
  currency?: Currency | null
  isFund?: boolean
  quote?: Quote | null
}>(), {
  mergeByPrice: true,
})
const emit = defineEmits<{ setTarget: [lot: Lot]; sell: [payload: LotSellPayload] }>()
const { isMobile } = useViewport()
const tableRef = ref<TableInstance | null>(null)
const expandedMobileKeys = ref<Record<string, boolean>>({})
const qtyTarget = computed(() => ({ currency: props.currency, securityType: props.isFund ? 'FUND' : 'STOCK' }))
const displayRows = computed(() => buildLotDisplayRows(props.lots, props.mergeByPrice))

/** 返回批次当前成本总价：持有中展示剩余成本，已结清展示初始总成本。 */
function lotCostTotal(row: Pick<LotDisplayRow, 'status' | 'remainingCost' | 'originalCost'>): string {
  return row.status === 'OPEN' ? row.remainingCost : row.originalCost
}

/** 计算持有中批次或合并组的展示用当前总价值（剩余数量 × 最新价），缺行情时返回空值。 */
function lotMarketValue(row: Pick<LotDisplayRow, 'status' | 'remainingQty'>): string | null {
  if (row.status !== 'OPEN') return null
  const price = props.quote?.lastPrice
  if (price === null || price === undefined || price === '') return null
  const qty = Number(row.remainingQty)
  const latest = Number(price)
  if (!Number.isFinite(qty) || !Number.isFinite(latest) || latest <= 0) return null
  const total = qty * latest
  return Number.isFinite(total) ? total.toFixed(2) : null
}

/** 判断单个持有中批次在盘中估算净值下是否已预估触达有效止盈目标价（不落库固化）。 */
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

/** 判断展示行（单批或合并组）是否存在预估达标的持有中批次。 */
function isRowEstimatedTargetReached(row: LotDisplayRow): boolean {
  return row.lots.some((lot) => isEstimatedTargetReached(lot))
}

/** 展示仍可操作的分段目标，已完成和取消记录不再掩盖上级继承。 */
function formatTargets(row: Pick<LotDisplayRow, 'targets'>): string {
  const digits = priceDigits(props.isFund, 3)
  const unit = quantityUnit(props.isFund)
  return (row.targets ?? []).filter((target) => !['CANCELLED', 'COMPLETED'].includes(target.status))
    .map((target) => `${formatMoney(target.targetPriceEstimate, props.currency, digits)} / ${formatTargetPercent(target.targetReturnPct)} · ${formatQuantity(target.plannedSellQty, qtyTarget.value)} ${unit}${target.status === 'TRIGGERED' ? '（已触发）' : target.status === 'CONFLICT' ? '（计划量冲突）' : ''}`).join(' · ')
}

/** 构造合并行悬浮提示文本，展示所有原始批次 ID。 */
function mergedIdsTooltip(row: LotDisplayRow): string {
  return row.lots.map((lot) => lot.id).join('\n')
}

/** 切换桌面端合并行的子批次展开状态。 */
function toggleDesktopGroup(row: LotDisplayRow): void {
  if (!row.isMerged) return
  tableRef.value?.toggleRowExpansion(row)
}

/** 切换移动端合并卡片的子批次展开状态。 */
function toggleMobileGroup(rowKey: string): void {
  expandedMobileKeys.value[rowKey] = !expandedMobileKeys.value[rowKey]
}

/** 返回移动端合并卡片当前是否已展开。 */
function isMobileGroupExpanded(rowKey: string): boolean {
  return Boolean(expandedMobileKeys.value[rowKey])
}

/** 根据单批次或合并组行构造预选卖出载荷并通知父组件打开手动关联工作台。 */
function emitSellRow(row: LotDisplayRow): void {
  const matches: ManualSellMatchItem[] = row.lots
    .filter((lot) => lot.status === 'OPEN')
    .map((lot) => ({ lotId: lot.id, matchedQty: lot.remainingQty }))
  emit('sell', { qty: row.remainingQty, matches })
}
</script>
<template>
  <el-table
    v-if="!isMobile"
    ref="tableRef"
    :data="displayRows"
    row-key="rowKey"
    :tree-props="{ children: 'children' }"
    stripe
    empty-text="暂无批次"
    class="lot-table"
  >
    <el-table-column label="批次 ID" min-width="115">
      <template #default="{ row }">
        <el-tag
          v-if="row.isMerged"
          type="primary"
          effect="plain"
          size="small"
          class="lot-merge-tag"
          :title="mergedIdsTooltip(row)"
          @click.stop="toggleDesktopGroup(row)"
        >
          合并 {{ row.lotCount }} 批
        </el-tag>
        <span v-else class="lot-code" :title="row.id">{{ row.id.slice(0, 8) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="买入日期" min-width="155">
      <template #default="{ row }">
        <div v-if="row.isMerged" class="lot-cell-lines">
          <div>{{ formatDateTime(row.executedAt) }}</div>
          <div class="lot-sub muted">最近买入 · 共 {{ row.lotCount }} 笔</div>
        </div>
        <template v-else>{{ formatDateTime(row.executedAt) }}</template>
      </template>
    </el-table-column>
    <el-table-column :label="isFund ? '份额 / 单位成本' : '股数 / 单位成本'" min-width="185">
      <template #default="{ row }">
        <div class="lot-cell-lines">
          <div class="lot-primary num">
            剩 {{ formatQuantity(row.remainingQty, qtyTarget) }}
            <span class="muted">/ 初 {{ formatQuantity(row.originalQty, qtyTarget) }}</span>
          </div>
          <div v-if="row.isMerged" class="lot-sub muted num">
            买入价 {{ formatMoney(row.buyPrice, currency, priceDigits(isFund, 3)) }} · 含费均价 {{ formatMoney(row.unitCost, currency, priceDigits(isFund, 3)) }}
          </div>
          <div v-else class="lot-sub muted num">含费单价 {{ formatMoney(row.unitCost, currency, priceDigits(isFund, 3)) }}</div>
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
          <template v-if="row.isMerged">
            <div v-if="row.explicitTargetLotCount > 0" class="lot-target-line muted">
              止盈 含 {{ row.explicitTargetLotCount }} 批独立设置（展开查看）
            </div>
            <div v-else-if="row.effectiveTarget && row.effectiveTarget.source !== 'LOT'" class="lot-target-line">
              <span class="num">
                止盈
                <template v-if="row.effectiveTarget.targetPriceEstimate">
                  {{ formatMoney(row.effectiveTarget.targetPriceEstimate, currency, priceDigits(isFund, 3)) }} /
                </template>
                {{ formatTargetPercent(row.effectiveTarget.targetReturnPct) }}
              </span>
              <span class="muted lot-inline-note">（继承{{ row.effectiveTarget.source === 'SECURITY' ? '个股' : '全局' }}）</span>
            </div>
            <div v-else class="lot-target-line muted">止盈 未设置</div>
          </template>
          <template v-else>
            <div v-if="formatTargets(row)" class="lot-target-line">
              <span class="num">止盈 {{ formatTargets(row) }}</span>
              <span class="muted lot-inline-note">（批次设置）</span>
            </div>
            <div v-if="row.effectiveTarget && row.effectiveTarget.source !== 'LOT'" class="lot-target-line">
              <span class="num">止盈 {{ formatMoney(row.effectiveTarget.targetPriceEstimate, currency, priceDigits(isFund, 3)) }} / {{ formatTargetPercent(row.effectiveTarget.targetReturnPct) }}</span>
              <span class="muted lot-inline-note">（继承{{ row.effectiveTarget.source === 'SECURITY' ? '个股' : '全局' }}）</span>
            </div>
            <div v-else-if="!formatTargets(row)" class="lot-target-line muted">止盈 未设置</div>
          </template>
          <el-tag v-if="isRowEstimatedTargetReached(row)" type="warning" size="small" class="lot-target-tag">预估达标</el-tag>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="操作" width="76">
      <template #default="{ row }">
        <div v-if="row.status === 'OPEN'" class="lot-actions">
          <el-button link type="danger" @click="emitSellRow(row)">卖出</el-button>
          <el-button v-if="!row.isMerged && row.lot" link type="primary" @click="emit('setTarget', row.lot)">设置</el-button>
          <el-button v-else link type="primary" @click="toggleDesktopGroup(row)">明细</el-button>
        </div>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
  </el-table>
  <div v-else class="m-lot-list">
    <div v-if="!displayRows.length" class="m-empty">暂无批次</div>
    <article v-for="row in displayRows" :key="row.rowKey" class="m-lot-item">
      <div class="m-lot-head">
        <div class="m-lot-meta">
          <button
            v-if="row.isMerged"
            type="button"
            class="m-merge-btn"
            :aria-expanded="isMobileGroupExpanded(row.rowKey)"
            @click="toggleMobileGroup(row.rowKey)"
          >
            合并 {{ row.lotCount }} 批 {{ isMobileGroupExpanded(row.rowKey) ? '▴' : '▾' }}
          </button>
          <span v-else class="lot-code" :title="row.id">{{ row.id.slice(0, 8) }}</span>
          <el-tag :type="row.status === 'OPEN' ? 'success' : 'info'" size="small">{{ row.status === 'OPEN' ? '持有中' : '已结清' }}</el-tag>
          <span class="m-lot-time num">{{ formatDateTime(row.executedAt) }}</span>
        </div>
        <div v-if="row.status === 'OPEN'" class="m-lot-actions">
          <el-button link type="danger" size="small" @click="emitSellRow(row)">卖出</el-button>
          <el-button v-if="!row.isMerged && row.lot" link type="primary" size="small" @click="emit('setTarget', row.lot)">设置</el-button>
        </div>
      </div>
      <div class="m-lot-grid">
        <div class="m-lot-cell">
          <span class="m-lot-label">剩余 / 初始</span>
          <strong class="m-lot-val num">{{ formatQuantity(row.remainingQty, qtyTarget) }} / {{ formatQuantity(row.originalQty, qtyTarget) }}</strong>
          <span v-if="row.isMerged" class="m-lot-sub num">买价 {{ formatMoney(row.buyPrice, currency, priceDigits(isFund, 3)) }}</span>
          <span v-else class="m-lot-sub num">单价 {{ formatMoney(row.unitCost, currency, priceDigits(isFund, 3)) }}</span>
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
        <template v-if="row.isMerged">
          <span v-if="row.explicitTargetLotCount > 0" class="m-lot-source">含 {{ row.explicitTargetLotCount }} 批独立设置</span>
          <template v-else-if="row.effectiveTarget && row.effectiveTarget.source !== 'LOT'">
            <span class="num">
              <template v-if="row.effectiveTarget.targetPriceEstimate">
                {{ formatMoney(row.effectiveTarget.targetPriceEstimate, currency, priceDigits(isFund, 3)) }} /
              </template>
              {{ formatTargetPercent(row.effectiveTarget.targetReturnPct) }}
            </span>
            <span class="m-lot-source">（继承{{ row.effectiveTarget.source === 'SECURITY' ? '个股' : '全局' }}）</span>
          </template>
          <span v-else class="m-lot-source">未设置</span>
        </template>
        <template v-else>
          <template v-if="formatTargets(row)">
            <span class="num">{{ formatTargets(row) }}</span>
            <span class="m-lot-source">（批次设置）</span>
          </template>
          <template v-if="row.effectiveTarget && row.effectiveTarget.source !== 'LOT'">
            <span class="num">{{ formatMoney(row.effectiveTarget.targetPriceEstimate, currency, priceDigits(isFund, 3)) }} / {{ formatTargetPercent(row.effectiveTarget.targetReturnPct) }}</span>
            <span class="m-lot-source">（继承{{ row.effectiveTarget.source === 'SECURITY' ? '个股' : '全局' }}）</span>
          </template>
          <span v-else-if="!formatTargets(row)" class="m-lot-source">未设置</span>
        </template>
        <el-tag v-if="isRowEstimatedTargetReached(row)" type="warning" size="small" style="margin-left:6px">预估达标</el-tag>
      </div>
      <div v-if="row.isMerged && row.children && isMobileGroupExpanded(row.rowKey)" class="m-lot-children">
        <div v-for="child in row.children" :key="child.rowKey" class="m-lot-child">
          <div class="m-lot-child-head">
            <div class="m-lot-meta">
              <span class="lot-code" :title="child.id">{{ child.id.slice(0, 8) }}</span>
              <span class="m-lot-time num">{{ formatDateTime(child.executedAt) }}</span>
            </div>
            <div v-if="child.status === 'OPEN'" class="m-lot-actions">
              <el-button link type="danger" size="small" @click="emitSellRow(child)">卖出</el-button>
              <el-button v-if="child.lot" link type="primary" size="small" @click="emit('setTarget', child.lot)">设置</el-button>
            </div>
          </div>
          <div class="m-lot-child-body num">
            <span>剩 {{ formatQuantity(child.remainingQty, qtyTarget) }} / 初 {{ formatQuantity(child.originalQty, qtyTarget) }}</span>
            <span>单价 {{ formatMoney(child.unitCost, currency, priceDigits(isFund, 3)) }}</span>
            <span v-if="child.status === 'OPEN'" :class="trendClass(child.unrealizedPnl)">{{ formatMoney(child.unrealizedPnl, currency) }}</span>
          </div>
        </div>
      </div>
    </article>
  </div>
</template>
<style scoped>
.lot-code { border-bottom: 1px dotted currentColor; cursor: help; font-family: ui-monospace, monospace; }
.lot-merge-tag { cursor: pointer; }
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
.m-merge-btn { height: 22px; padding: 0 6px; border: 1px solid #bfdbfe; border-radius: 4px; background: #eff6ff; color: #1d4ed8; font-size: 11px; font-weight: 600; cursor: pointer; white-space: nowrap; }
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
.m-lot-children { margin-top: 6px; padding: 6px 8px; border-radius: 6px; background: #f8fafc; border: 1px solid #edf0f5; display: flex; flex-direction: column; gap: 6px; }
.m-lot-child { padding-bottom: 6px; border-bottom: 1px dashed #e2e8f0; }
.m-lot-child:last-child { padding-bottom: 0; border-bottom: 0; }
.m-lot-child-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 2px; }
.m-lot-child-body { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; font-size: 11px; color: #334155; }
</style>
