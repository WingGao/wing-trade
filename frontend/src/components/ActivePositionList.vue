<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Currency, DashboardPosition, PositionRefreshScope, Quote } from '../api/types'
import {
  formatCompactMoney,
  formatDateTime,
  formatDecimal,
  formatPercent,
  formatQuantity,
  formatSignedMoney,
  formatSignedPercent,
  isFundSecurity,
  marketSessionLabel,
  priceDigits,
  quantityUnit,
  trendClass,
} from '../utils/format'
import { isPositionRefreshing } from '../utils/recalculate'

export type PositionFilterTab = 'all' | 'lowestProfit' | 'belowLastSell' | 'loss'
export type PositionSortField = 'marketValue' | 'unrealizedPnl' | 'changePct' | 'lowestReturnPct' | 'vsLastSellPct'
export type PositionSortOrder = 'descending' | 'ascending'

const props = withDefaults(defineProps<{
  positions: DashboardPosition[]
  totalOpenPositions: number
  currency: Currency | null
  accountId?: string
  loading?: boolean
  activeKey?: string | null
  pendingScopes?: PositionRefreshScope[]
  unlistedScopes?: PositionRefreshScope[]
}>(), {
  accountId: '',
  loading: false,
  activeKey: null,
  pendingScopes: () => [],
  unlistedScopes: () => [],
})

const emit = defineEmits<{
  (e: 'select', position: DashboardPosition): void
  (e: 'recalculate', scope: PositionRefreshScope): void
}>()

const filterTab = ref<PositionFilterTab>('all')
const keyword = ref('')
const compactMode = ref(false)
const sortField = ref<PositionSortField>('changePct')
const sortOrder = ref<PositionSortOrder>('descending')

/** 返回持仓行的稳定唯一键。 */
function rowKey(position: DashboardPosition): string {
  return `${position.securityId}|${position.strategyId}`
}

/** 构造行级待刷新范围，供重算防重与提交使用。 */
function rowScope(position: DashboardPosition): PositionRefreshScope {
  return {
    accountId: props.accountId,
    securityId: position.securityId,
    strategyId: position.strategyId,
    market: position.market,
    symbol: position.symbol,
    name: position.name,
  }
}

/** 计算持仓当前展示总市值（数值），缺失有效行情时返回空值。 */
function positionMarketValueNum(position: DashboardPosition): number | null {
  const price = position.quote?.lastPrice
  if (price === null || price === undefined || price === '') return null
  const qty = Number(position.remainingQty)
  const latest = Number(price)
  if (!Number.isFinite(qty) || !Number.isFinite(latest)) return null
  const value = qty * latest
  return Number.isFinite(value) ? value : null
}

/** 计算持仓当前展示总市值字符串（两位小数），缺行情返回空值。 */
function positionMarketValueText(position: DashboardPosition): string | null {
  const value = positionMarketValueNum(position)
  return value === null ? null : value.toFixed(2)
}

/** 将十进制字符串解析为有限数值，无效或空值返回 null。 */
function parseFiniteNumber(raw: string | null | undefined): number | null {
  if (raw === null || raw === undefined || raw === '') return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : null
}

/** 判断持仓是否命中“低批盈利”筛选条件。 */
function isLowestProfit(position: DashboardPosition): boolean {
  const pnl = parseFiniteNumber(position.lowestPriceGroup?.unrealizedPnl)
  return pnl !== null && pnl > 0
}

/** 判断持仓是否命中“低于上卖”筛选条件（有效现价低于上次卖出价）。 */
function isBelowLastSell(position: DashboardPosition): boolean {
  const diffPct = parseFiniteNumber(position.vsLastSellPct)
  return diffPct !== null && diffPct < 0
}

/** 判断持仓是否命中“浮亏仓位”筛选条件。 */
function isLossPosition(position: DashboardPosition): boolean {
  const pnl = parseFiniteNumber(position.unrealizedPnl)
  return pnl !== null && pnl < 0
}

/** 计算四个筛选标签的数量角标，始终基于账户全量活动持仓。 */
const filterCounts = computed(() => {
  let lowestProfit = 0
  let belowLastSell = 0
  let loss = 0
  for (const pos of props.positions) {
    if (isLowestProfit(pos)) lowestProfit += 1
    if (isBelowLastSell(pos)) belowLastSell += 1
    if (isLossPosition(pos)) loss += 1
  }
  return {
    all: props.positions.length,
    lowestProfit,
    belowLastSell,
    loss,
  }
})

/** 按指定排序字段提取可比较数值，缺失值统一返回 null 以便置后。 */
function sortMetricValue(position: DashboardPosition, field: PositionSortField): number | null {
  switch (field) {
    case 'marketValue':
      return positionMarketValueNum(position)
    case 'unrealizedPnl':
      return parseFiniteNumber(position.unrealizedPnl)
    case 'changePct':
      return parseFiniteNumber(position.quote?.changePct)
    case 'lowestReturnPct':
      return parseFiniteNumber(position.lowestPriceGroup?.unrealizedReturnPct)
    case 'vsLastSellPct':
      return parseFiniteNumber(position.vsLastSellPct)
  }
}

/** 切换排序字段与方向：同一字段正反切换，新字段默认降序，无“取消排序”态。 */
function toggleSort(field: PositionSortField): void {
  if (sortField.value === field) {
    sortOrder.value = sortOrder.value === 'descending' ? 'ascending' : 'descending'
    return
  }
  sortField.value = field
  sortOrder.value = 'descending'
}

/** 返回指定表头字段的排序箭头文本，未激活时返回空串。 */
function sortArrow(field: PositionSortField): string {
  if (sortField.value !== field) return ''
  return sortOrder.value === 'descending' ? '↓' : '↑'
}

/** 切换标准与紧凑行高视图。 */
function toggleCompactMode(): void {
  compactMode.value = !compactMode.value
}

/** 返回经过筛选、代码/名称搜索和当前排序后的活动仓位列表。 */
const visiblePositions = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  const filtered = props.positions.filter((pos) => {
    if (filterTab.value === 'lowestProfit' && !isLowestProfit(pos)) return false
    if (filterTab.value === 'belowLastSell' && !isBelowLastSell(pos)) return false
    if (filterTab.value === 'loss' && !isLossPosition(pos)) return false
    if (!q) return true
    const code = `${pos.market ?? ''}:${pos.symbol ?? ''}`.toLowerCase()
    const sym = (pos.symbol ?? '').toLowerCase()
    const name = (pos.name ?? '').toLowerCase()
    return code.includes(q) || sym.includes(q) || name.includes(q)
  })
  const direction = sortOrder.value === 'ascending' ? 1 : -1
  const field = sortField.value
  return [...filtered].sort((left, right) => {
    const leftVal = sortMetricValue(left, field)
    const rightVal = sortMetricValue(right, field)
    if (leftVal === null && rightVal === null) return 0
    if (leftVal === null) return 1
    if (rightVal === null) return -1
    return (leftVal - rightVal) * direction
  })
})

/** 返回需要展示在现价旁的异常/特殊报价状态徽标文案，正常实时行情返回空串。 */
function specialQuoteBadge(quote: Quote | null): string {
  if (!quote || quote.lastPrice === null || quote.lastPrice === undefined) return '不可用'
  if (quote.isStale) return '过期'
  if (quote.isEstimated) return '估'
  if (quote.marketSession === 'pre') return '盘前'
  if (quote.marketSession === 'post') return '盘后'
  return ''
}

/** 格式化现价数字（股票 3 位、基金 4 位，不带币种符号以对齐三行价格列）。 */
function formatUnitPrice(value: string | null | undefined, position: DashboardPosition): string {
  return formatDecimal(value, priceDigits(position, 3))
}

/** 格式化报价提示中的估算参考说明。 */
function estimateSourceText(quote: Quote): string | null {
  if (!quote.isEstimated || !quote.proxySymbol) return null
  const label = quote.proxyName || quote.proxySymbol
  const proxyChg = quote.proxyChangePct ? formatSignedPercent(quote.proxyChangePct) : ''
  const ratioNum = quote.trackingRatio ? Number(quote.trackingRatio) * 100 : 95
  const ratioLabel = Number.isFinite(ratioNum)
    ? `${Number.isInteger(ratioNum) ? ratioNum : ratioNum.toFixed(1).replace(/\.0$/, '')}%`
    : '95%'
  return `参考 ${label}${proxyChg ? ` ${proxyChg}` : ''} × ${ratioLabel}`
}

/** 生成近 20 个交易日平均振幅的悬停口径说明文案。 */
function amplitudeTooltipText(position: DashboardPosition): string {
  if (isFundSecurity(position)) return '场外基金不统计日内振幅'
  if (position.avgAmplitude20d !== null && position.avgAmplitude20d !== undefined && position.avgAmplitude20d !== '') {
    const days = position.avgAmplitudeDays ?? 20
    const asOf = position.avgAmplitudeAsOf ? `（截至 ${position.avgAmplitudeAsOf}）` : ''
    return `最近 ${days} 个已收盘交易日 (最高-最低)/昨收 算术均值${asOf}`
  }
  return '暂无近 20 个已收盘交易日振幅数据'
}

/** 触发选中行事件，由父页面决定联动右栏还是路由跳转。 */
function handleSelect(position: DashboardPosition): void {
  emit('select', position)
}

/** 触发持仓重算事件。 */
function handleRecalculate(scope: PositionRefreshScope): void {
  emit('recalculate', scope)
}

defineExpose({
  visiblePositions,
})
</script>

<template>
  <section :class="['ap-card', { 'is-compact': compactMode }]" v-loading="loading" aria-label="活动仓位">
    <div class="ap-head">
      <div class="ap-head-title">
        <strong>活动仓位</strong>
        <span class="ap-head-count">{{ totalOpenPositions }} 个未结清持仓</span>
      </div>
      <button
        type="button"
        :class="['ap-compact-btn', { 'is-active': compactMode }]"
        :aria-pressed="compactMode"
        @click="toggleCompactMode"
      >
        <span class="ap-compact-icon" aria-hidden="true">☰</span>
        <span>紧凑</span>
      </button>
    </div>

    <div class="ap-controls">
      <div class="ap-filter-group" role="tablist" aria-label="活动仓位筛选">
        <button
          type="button"
          role="tab"
          :aria-selected="filterTab === 'all'"
          :class="['ap-filter-tab', { 'is-active': filterTab === 'all' }]"
          @click="filterTab = 'all'"
        >
          全部 <span class="ap-tab-count num">{{ filterCounts.all }}</span>
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="filterTab === 'lowestProfit'"
          :class="['ap-filter-tab', { 'is-active': filterTab === 'lowestProfit' }]"
          @click="filterTab = 'lowestProfit'"
        >
          低批盈利 <span class="ap-tab-count num">{{ filterCounts.lowestProfit }}</span>
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="filterTab === 'belowLastSell'"
          :class="['ap-filter-tab', { 'is-active': filterTab === 'belowLastSell' }]"
          @click="filterTab = 'belowLastSell'"
        >
          低于上卖 <span class="ap-tab-count num">{{ filterCounts.belowLastSell }}</span>
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="filterTab === 'loss'"
          :class="['ap-filter-tab', { 'is-active': filterTab === 'loss' }]"
          @click="filterTab = 'loss'"
        >
          浮亏仓位 <span class="ap-tab-count num">{{ filterCounts.loss }}</span>
        </button>
      </div>
      <el-input
        v-model="keyword"
        size="small"
        clearable
        placeholder="代码 / 名称"
        class="ap-search-input"
      />
    </div>

    <div v-if="pendingScopes.length" class="ap-notice is-warning">
      需要刷新：部分交易流水已更正，相关持仓、收益及账户汇总尚未重新核算。
    </div>
    <div
      v-for="scope in unlistedScopes"
      :key="`${scope.accountId}|${scope.securityId}|${scope.strategyId}`"
      class="ap-pending-scope"
    >
      <span class="ap-pending-text">
        <strong>{{ scope.market }}:{{ scope.symbol }}</strong> {{ scope.name ?? '' }} · 当前无活动仓位 · 需要刷新
      </span>
      <el-button
        size="small"
        type="warning"
        :loading="isPositionRefreshing(scope)"
        @click="handleRecalculate(scope)"
      >
        刷新持仓
      </el-button>
    </div>

    <div class="ap-table-head">
      <span class="ap-th-sec">证券 / 持仓</span>
      <div class="ap-th-val">
        <button
          type="button"
          :class="['ap-sort-btn', { 'is-active': sortField === 'marketValue' }]"
          @click="toggleSort('marketValue')"
        >
          总市值<span v-if="sortArrow('marketValue')" class="ap-sort-arrow">{{ sortArrow('marketValue') }}</span>
        </button>
        <span class="ap-th-sep">/</span>
        <button
          type="button"
          :class="['ap-sort-btn', { 'is-active': sortField === 'unrealizedPnl' }]"
          @click="toggleSort('unrealizedPnl')"
        >
          浮盈<span v-if="sortArrow('unrealizedPnl')" class="ap-sort-arrow">{{ sortArrow('unrealizedPnl') }}</span>
        </button>
      </div>
      <div class="ap-th-prices">
        <button
          type="button"
          :class="['ap-sort-btn', { 'is-active': sortField === 'changePct' }]"
          @click="toggleSort('changePct')"
        >
          现价<span v-if="sortArrow('changePct')" class="ap-sort-arrow">{{ sortArrow('changePct') }}</span>
        </button>
        <span class="ap-th-dot">·</span>
        <button
          type="button"
          :class="['ap-sort-btn', { 'is-active': sortField === 'lowestReturnPct' }]"
          @click="toggleSort('lowestReturnPct')"
        >
          低批<span v-if="sortArrow('lowestReturnPct')" class="ap-sort-arrow">{{ sortArrow('lowestReturnPct') }}</span>
        </button>
        <span class="ap-th-dot">·</span>
        <button
          type="button"
          :class="['ap-sort-btn', { 'is-active': sortField === 'vsLastSellPct' }]"
          @click="toggleSort('vsLastSellPct')"
        >
          上卖<span v-if="sortArrow('vsLastSellPct')" class="ap-sort-arrow">{{ sortArrow('vsLastSellPct') }}</span>
        </button>
      </div>
    </div>

    <div v-if="visiblePositions.length" class="ap-list">
      <article
        v-for="row in visiblePositions"
        :key="rowKey(row)"
        :class="['ap-row', { 'is-active': activeKey === rowKey(row) }]"
        @click="handleSelect(row)"
      >
        <div class="ap-row-grid">
          <div class="ap-col-sec">
            <a class="ap-code-link" href="#" @click.prevent.stop="handleSelect(row)">
              {{ row.market ? `${row.market}:${row.symbol}` : row.securityId }}
            </a>
            <div v-if="row.name" class="ap-sec-name" :title="row.name">{{ row.name }}</div>
            <div class="ap-sec-holding num">
              {{ formatQuantity(row.remainingQty, row) }}{{ quantityUnit(row) }} · 均 {{ formatCompactMoney(row.averageCost, row.currency ?? currency, priceDigits(row, 2)) }}
            </div>
          </div>

          <div class="ap-col-val num">
            <div class="ap-mv">
              {{ formatCompactMoney(positionMarketValueText(row), row.currency ?? currency, 2) }}
            </div>
            <div class="ap-pnl-line">
              <span :class="['ap-pnl-amt', trendClass(row.unrealizedPnl)]">
                {{ formatSignedMoney(row.unrealizedPnl, row.currency ?? currency, 2) }}
              </span>
              <span :class="['ap-pnl-pct', trendClass(row.unrealizedReturnPct)]">
                {{ formatSignedPercent(row.unrealizedReturnPct) }}
              </span>
            </div>
            <div class="ap-lowest-line">
              <template v-if="row.lowestPriceGroup">
                <span class="ap-lowest-meta">低批 {{ formatQuantity(row.lowestPriceGroup.remainingQty, row) }}{{ quantityUnit(row) }}</span>
                <span class="ap-lowest-dot">·</span>
                <span :class="['ap-lowest-pnl', trendClass(row.lowestPriceGroup.unrealizedPnl)]">
                  {{ formatSignedMoney(row.lowestPriceGroup.unrealizedPnl, row.currency ?? currency, 2) }}
                </span>
              </template>
              <span v-else class="ap-muted">无低批</span>
            </div>
          </div>

          <div class="ap-col-prices num">
            <el-tooltip placement="top" :show-after="200" :disabled="!row.quote">
              <template #content>
                <div v-if="row.quote" class="ap-quote-tip">
                  <strong>{{ row.quote.isEstimated ? '盘中估算净值' : '报价更新信息' }}</strong>
                  <span v-if="estimateSourceText(row.quote)">{{ estimateSourceText(row.quote) }}</span>
                  <span v-if="row.quote.isEstimated && row.quote.officialNav">
                    官方净值：{{ formatUnitPrice(row.quote.officialNav, row) }}<template v-if="row.quote.officialNavDate"> ({{ row.quote.officialNavDate }})</template>
                  </span>
                  <span v-if="row.quote.marketSession">交易时段：{{ marketSessionLabel(row.quote.marketSession) }}</span>
                  <span v-if="row.quote.provider">行情来源：{{ row.quote.provider }}</span>
                  <span>报价时间：{{ row.quote.sourceTime ? formatDateTime(row.quote.sourceTime) : '未知' }}</span>
                  <span>获取时间：{{ row.quote.fetchedAt ? formatDateTime(row.quote.fetchedAt) : '未知' }}</span>
                  <span>{{ row.quote.isEstimated ? '估算净值' : isFundSecurity(row) ? '最新净值' : '当前价' }}：{{ formatUnitPrice(row.quote.lastPrice, row) }}</span>
                </div>
              </template>
              <div class="ap-price-row">
                <span class="ap-price-label">
                  现价<span v-if="specialQuoteBadge(row.quote)" class="ap-quote-flag">{{ specialQuoteBadge(row.quote) }}</span>
                </span>
                <strong class="ap-price-num ap-price-current">
                  {{ row.quote?.lastPrice ? formatUnitPrice(row.quote.lastPrice, row) : '不可用' }}
                </strong>
                <span :class="['ap-price-pct', trendClass(row.quote?.changePct)]">
                  {{ formatSignedPercent(row.quote?.changePct) }}
                </span>
              </div>
            </el-tooltip>

            <div class="ap-price-row">
              <span class="ap-price-label">低批</span>
              <span class="ap-price-num">
                {{ row.lowestPriceGroup ? formatUnitPrice(row.lowestPriceGroup.purchasePrice, row) : '—' }}
              </span>
              <span :class="['ap-price-pct', trendClass(row.lowestPriceGroup?.unrealizedReturnPct)]">
                {{ formatSignedPercent(row.lowestPriceGroup?.unrealizedReturnPct) }}
              </span>
            </div>

            <div class="ap-price-row">
              <span class="ap-price-label">上卖</span>
              <span :class="['ap-price-num', { 'ap-no-sell': !row.lastSellPrice }]">
                {{ row.lastSellPrice ? formatUnitPrice(row.lastSellPrice, row) : '暂无卖出' }}
              </span>
              <span :class="['ap-price-pct', trendClass(row.vsLastSellPct)]">
                {{ row.lastSellPrice ? formatSignedPercent(row.vsLastSellPct) : '—' }}
              </span>
            </div>

            <el-tooltip placement="top" :show-after="200">
              <template #content>
                <div class="ap-quote-tip">
                  <strong>近 20 日平均振幅</strong>
                  <span>{{ amplitudeTooltipText(row) }}</span>
                </div>
              </template>
              <div class="ap-price-row ap-amplitude-row">
                <span class="ap-price-label">20日振</span>
                <span class="ap-price-num ap-amplitude-spacer" aria-hidden="true" />
                <span class="ap-price-pct ap-amplitude-val">
                  {{ formatPercent(row.avgAmplitude20d) }}
                </span>
              </div>
            </el-tooltip>
          </div>
        </div>

        <div v-if="row.needsRefresh" class="ap-row-refresh" @click.stop>
          <span class="ap-refresh-hint">需要刷新：交易流水已更正，待重新核算</span>
          <el-button
            size="small"
            type="warning"
            plain
            :loading="isPositionRefreshing(rowScope(row))"
            @click.stop="handleRecalculate(rowScope(row))"
          >
            刷新持仓
          </el-button>
        </div>
      </article>
    </div>

    <div v-else class="ap-empty">
      {{ positions.length ? '当前筛选或搜索条件下暂无匹配持仓' : '暂无持仓' }}
    </div>
  </section>
</template>

<style scoped>
.ap-card {
  background: #fff;
  border: 1px solid #e9edf3;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 4px 18px #1822300b;
}

.ap-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px 8px;
}

.ap-head-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.ap-head-title strong {
  font-size: 16px;
  font-weight: 700;
  color: #182230;
}

.ap-head-count {
  font-size: 12px;
  color: #7d899b;
}

.ap-compact-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 9px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: #475569;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
}

.ap-compact-btn:hover {
  background: #f1f5f9;
}

.ap-compact-btn.is-active {
  border-color: #bfdbfe;
  background: #eff6ff;
  color: #2563eb;
}

.ap-compact-icon {
  font-size: 12px;
  line-height: 1;
}

.ap-controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  padding: 0 14px 10px;
}

.ap-filter-group {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
  padding: 3px;
  background: #f3f5f9;
  border-radius: 8px;
}

.ap-filter-tab {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 9px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #4b5563;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.ap-filter-tab:hover {
  color: #111827;
}

.ap-filter-tab.is-active {
  background: #fff;
  color: #111827;
  font-weight: 700;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
}

.ap-tab-count {
  color: #94a3b8;
  font-size: 11px;
  font-weight: 600;
}

.ap-filter-tab.is-active .ap-tab-count {
  color: #2563eb;
}

.ap-search-input {
  width: 156px;
  min-width: 120px;
  flex: 0 1 156px;
}

.ap-notice {
  padding: 6px 14px;
  font-size: 12px;
  line-height: 1.4;
  border-top: 1px solid #edf0f5;
}

.ap-notice.is-warning {
  background: #fdf6ec;
  color: #a66a14;
}

.ap-pending-scope {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 14px;
  background: #fffbf2;
  border-top: 1px solid #f5e6c8;
  font-size: 12px;
}

.ap-pending-text {
  min-width: 0;
  overflow-wrap: anywhere;
}

.ap-table-head {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1.05fr) minmax(0, 1.25fr);
  gap: 8px;
  align-items: center;
  padding: 8px 14px;
  background: #fafbfd;
  border-top: 1px solid #edf0f5;
  border-bottom: 1px solid #edf0f5;
  font-size: 12px;
  color: #64748b;
}

.ap-th-sec {
  font-weight: 500;
}

.ap-th-val,
.ap-th-prices {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 3px;
  min-width: 0;
  white-space: nowrap;
}

.ap-th-sep,
.ap-th-dot {
  color: #cbd5e1;
  font-size: 11px;
}

.ap-sort-btn {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  border: 0;
  background: transparent;
  padding: 1px 3px;
  border-radius: 4px;
  color: #64748b;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
}

.ap-sort-btn:hover {
  color: #1e293b;
  background: #eef2f7;
}

.ap-sort-btn.is-active {
  color: #2563eb;
  font-weight: 700;
}

.ap-sort-arrow {
  font-size: 11px;
  line-height: 1;
}

.ap-list {
  display: flex;
  flex-direction: column;
}

.ap-row {
  border-bottom: 1px solid #edf0f5;
  border-left: 3px solid transparent;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.ap-row:last-child {
  border-bottom: 0;
}

.ap-row:hover {
  background: #f8fafd;
}

.ap-row.is-active {
  background: #f0f6ff;
  border-left-color: #2563eb;
}

.ap-row-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1.05fr) minmax(0, 1.25fr);
  gap: 8px;
  align-items: center;
  padding: 12px 14px 12px 11px;
}

.ap-col-sec {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.ap-code-link {
  color: #2563eb;
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
  line-height: 1.25;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ap-code-link:hover {
  text-decoration: underline;
}

.ap-sec-name {
  font-size: 14px;
  font-weight: 700;
  color: #182230;
  line-height: 1.25;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ap-sec-holding {
  font-size: 11.5px;
  color: #7d899b;
  line-height: 1.25;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ap-col-val {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  text-align: right;
}

.ap-mv {
  font-size: 15px;
  font-weight: 700;
  color: #182230;
  line-height: 1.25;
  white-space: nowrap;
}

.ap-pnl-line {
  display: flex;
  align-items: baseline;
  justify-content: flex-end;
  gap: 5px;
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.25;
  white-space: nowrap;
}

.ap-lowest-line {
  display: flex;
  align-items: baseline;
  justify-content: flex-end;
  gap: 4px;
  font-size: 11.5px;
  color: #7d899b;
  line-height: 1.25;
  white-space: nowrap;
}

.ap-lowest-dot {
  color: #cbd5e1;
}

.ap-lowest-pnl {
  font-weight: 600;
}

.ap-muted {
  color: #94a3b8;
}

.ap-col-prices {
  min-width: 0;
  border-left: 1px solid #edf0f5;
  padding-left: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ap-price-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) minmax(48px, auto);
  align-items: baseline;
  gap: 6px;
  line-height: 1.25;
}

.ap-price-label {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11.5px;
  color: #8b98aa;
  white-space: nowrap;
}

.ap-quote-flag {
  display: inline-block;
  padding: 0 3px;
  border-radius: 3px;
  background: #fef3c7;
  color: #b45309;
  font-size: 9.5px;
  font-weight: 600;
  line-height: 1.4;
}

.ap-price-num {
  text-align: right;
  font-size: 12.5px;
  font-weight: 500;
  color: #334155;
  white-space: nowrap;
}

.ap-price-current {
  font-size: 13.5px;
  font-weight: 700;
  color: #182230;
}

.ap-no-sell {
  color: #94a3b8;
  font-weight: 400;
  font-size: 11.5px;
}

.ap-price-pct {
  text-align: right;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.ap-amplitude-val {
  color: #475569;
}

.ap-row-refresh {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 14px 7px;
  background: #fffbf2;
  border-top: 1px dashed #f5e6c8;
  font-size: 11.5px;
  color: #a66a14;
}

.ap-quote-tip {
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-width: 260px;
  line-height: 1.45;
  white-space: normal;
}

.ap-empty {
  padding: 28px 14px;
  text-align: center;
  font-size: 13px;
  color: #8b98aa;
}

/* 紧凑视图模式 */
.ap-card.is-compact .ap-row-grid {
  padding: 7px 12px 7px 9px;
}

.ap-card.is-compact .ap-col-sec,
.ap-card.is-compact .ap-col-val,
.ap-card.is-compact .ap-col-prices {
  gap: 1.5px;
}

.ap-card.is-compact .ap-sec-name {
  font-size: 13px;
}

.ap-card.is-compact .ap-mv {
  font-size: 13.5px;
}

.ap-card.is-compact .ap-price-current {
  font-size: 12.5px;
}

/* 移动端高密度适配（360px ~ 430px 零横向溢出） */
@media (max-width: 768px) {
  .ap-card {
    border-radius: 8px;
    box-shadow: none;
  }

  .ap-head {
    padding: 8px 10px 6px;
  }

  .ap-head-title strong {
    font-size: 14px;
  }

  .ap-head-count {
    font-size: 11px;
  }

  .ap-controls {
    padding: 0 10px 8px;
    gap: 6px;
  }

  .ap-filter-group {
    width: 100%;
    justify-content: space-between;
  }

  .ap-filter-tab {
    flex: 1 1 auto;
    justify-content: center;
    height: 25px;
    padding: 0 5px;
    font-size: 11px;
    gap: 2px;
  }

  .ap-tab-count {
    font-size: 10px;
  }

  .ap-search-input {
    width: 100%;
    flex: 1 1 100%;
  }

  .ap-table-head {
    grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.02fr) minmax(0, 1.24fr);
    gap: 4px;
    padding: 6px 8px;
    font-size: 10.5px;
  }

  .ap-sort-btn {
    padding: 0 2px;
    font-size: 10.5px;
  }

  .ap-row-grid {
    grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.02fr) minmax(0, 1.24fr);
    gap: 4px;
    padding: 8px 8px 8px 6px;
  }

  .ap-code-link {
    font-size: 11.5px;
  }

  .ap-sec-name {
    font-size: 12.5px;
  }

  .ap-sec-holding {
    font-size: 10px;
  }

  .ap-mv {
    font-size: 12.5px;
  }

  .ap-pnl-line {
    gap: 3px;
    font-size: 10.5px;
  }

  .ap-lowest-line {
    gap: 2px;
    font-size: 10px;
  }

  .ap-col-prices {
    padding-left: 6px;
    gap: 2px;
  }

  .ap-price-row {
    grid-template-columns: auto minmax(0, 1fr) minmax(40px, auto);
    gap: 3px;
  }

  .ap-price-label {
    font-size: 10px;
  }

  .ap-price-num {
    font-size: 11px;
  }

  .ap-price-current {
    font-size: 11.5px;
  }

  .ap-no-sell {
    font-size: 10px;
  }

  .ap-price-pct {
    font-size: 10.5px;
  }

  .ap-card.is-compact .ap-row-grid {
    padding: 5px 8px 5px 6px;
  }
}
</style>
