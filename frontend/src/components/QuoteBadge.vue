<script setup lang="ts">
import type { Currency, Quote } from '../api/types'
import { formatDateTime, formatMoney, formatSignedPercent, isFundSecurity, marketSessionLabel, priceDigits, trendClass } from '../utils/format'
import { useViewport } from '../utils/viewport'

const props = defineProps<{
  quote?: Quote | null
  currency?: Currency | null
  displayValue?: string | null
  compact?: boolean
}>()
const { isMobile } = useViewport()

/** 返回当前报价单价/净值的小数位（场外基金净值固定四位）。 */
function unitPriceDigits(quote: Quote): number {
  return priceDigits(quote, 3)
}

/** 返回主展示值；传入 displayValue 时用于展示持仓总价值并保留单价/净值回退。 */
function displayedPrice(quote: Quote): string {
  const hasDisplayValue = props.displayValue !== undefined
  return formatMoney(
    hasDisplayValue ? props.displayValue : quote.lastPrice,
    quote.currency ?? props.currency,
    hasDisplayValue ? 2 : unitPriceDigits(quote),
  )
}

/** 返回报价状态的文字，避免仅用颜色表达过期或盘前/盘后信息。 */
function quoteStatusLabel(quote: Quote): string {
  if (quote.lastPrice === null || quote.lastPrice === undefined) return '不可用'
  if (quote.isStale) return '已过期'
  if (isFundSecurity(quote)) return '净值'
  if (quote.marketSession === 'pre') return '盘前'
  if (quote.marketSession === 'post') return '盘后'
  return '实时'
}

/** 根据报价可用性、新鲜度与扩展时段选择状态标签样式。 */
function quoteStatusType(quote: Quote): 'success' | 'warning' | 'danger' | 'info' {
  if (quote.lastPrice === null || quote.lastPrice === undefined) return 'danger'
  if (quote.isStale) return 'warning'
  if (quote.marketSession === 'pre' || quote.marketSession === 'post') return 'info'
  return 'success'
}

/** 移动端紧凑模式在报价过期、不可用或处于盘前/盘后扩展时段时显示标签。 */
function showCompactStatusTag(quote: Quote): boolean {
  if (!isMobile.value) return true
  return (
    quote.lastPrice === null
    || quote.lastPrice === undefined
    || Boolean(quote.isStale)
    || quote.marketSession === 'pre'
    || quote.marketSession === 'post'
  )
}
</script>

<template>
  <span v-if="quote" class="quote-inline">
    <span :class="['quote-value-row', { 'quote-value-row--compact': props.compact }]">
      <strong>{{ displayedPrice(quote) }}</strong>
      <span v-if="!props.compact" class="quote-currency">{{ quote.currency ?? props.currency ?? '币种未知' }}</span>
      <span v-else class="quote-unit-price">{{ formatMoney(quote.lastPrice, quote.currency ?? props.currency, unitPriceDigits(quote)) }}</span>
      <span :class="['quote-change', trendClass(quote.changePct)]">{{ formatSignedPercent(quote.changePct) }}</span>
      <el-tooltip v-if="props.compact && showCompactStatusTag(quote)" placement="top" :show-after="150">
        <template #content>
          <div class="quote-tooltip">
            <strong>报价更新信息</strong>
            <span v-if="quote.marketSession">交易时段：{{ marketSessionLabel(quote.marketSession) }}</span>
            <span v-if="quote.provider">行情来源：{{ quote.provider }}</span>
            <span v-if="quote.sourceTime">报价时间：{{ formatDateTime(quote.sourceTime) }}</span>
            <span v-else>报价时间：未知</span>
            <span v-if="quote.fetchedAt">获取时间：{{ formatDateTime(quote.fetchedAt) }}</span>
            <span v-else>获取时间：未知</span>
            <span v-if="props.displayValue !== undefined">{{ isFundSecurity(quote) ? '最新净值' : '当前价' }}：{{ formatMoney(quote.lastPrice, quote.currency ?? props.currency, unitPriceDigits(quote)) }}</span>
          </div>
        </template>
        <span class="quote-status-trigger" tabindex="0">
          <el-tag :type="quoteStatusType(quote)" size="small">{{ quoteStatusLabel(quote) }}</el-tag>
        </span>
      </el-tooltip>
      <el-tag v-else-if="!props.compact" :type="quoteStatusType(quote)" size="small">{{ quoteStatusLabel(quote) }}</el-tag>
    </span>
    <span v-if="!props.compact" class="quote-meta">
      <span v-if="quote.provider">{{ quote.provider }}</span>
      <span v-if="quote.sourceTime">报价 {{ formatDateTime(quote.sourceTime) }}</span>
      <span v-else>报价时间未知</span>
      <span v-if="quote.fetchedAt">获取 {{ formatDateTime(quote.fetchedAt) }}</span>
    </span>
  </span>
  <span v-else class="unavailable">行情不可用</span>
</template>

<style scoped>
.quote-value-row--compact { display:grid; grid-template-columns:auto auto auto; justify-content:start; }
.quote-value-row--compact > strong { grid-column:1 / -1; }
.quote-unit-price { color:#657287; font-size:12px; font-weight:600; }
.quote-status-trigger { display:inline-flex; outline:none; }
.quote-status-trigger:focus-visible { border-radius:4px; box-shadow:0 0 0 2px rgba(64, 158, 255, .35); }
.quote-tooltip { display:flex; flex-direction:column; gap:3px; max-width:280px; line-height:1.45; white-space:normal; }
.quote-tooltip strong { margin-bottom:2px; }
@media (max-width: 768px) {
  .quote-value-row--compact { display: flex; flex-wrap: wrap; justify-content: flex-start; gap: 2px 4px; align-items: center; }
  .quote-value-row--compact > strong { flex: 1 0 100%; font-size: 13px; line-height: 1.25; white-space: nowrap; }
  .quote-value-row--compact .quote-unit-price { font-size: 11px; white-space: nowrap; }
  .quote-value-row--compact .quote-change { font-size: 11px; white-space: nowrap; }
  .quote-value-row--compact .quote-status-trigger { flex-shrink: 0; }
  .quote-value-row--compact :deep(.el-tag--small) { height: 18px; padding: 0 4px; font-size: 10px; line-height: 16px; }
}
</style>
