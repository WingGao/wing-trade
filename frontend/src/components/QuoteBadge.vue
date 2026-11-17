<script setup lang="ts">
import type { Currency, Quote } from '../api/types'
import { formatDateTime, formatMoney, formatSignedPercent, trendClass } from '../utils/format'

const props = defineProps<{ quote?: Quote | null; currency?: Currency | null }>()

/** 返回报价状态的文字，避免仅用颜色表达过期信息。 */
function quoteStatusLabel(quote: Quote): string {
  if (quote.lastPrice === null || quote.lastPrice === undefined) return '不可用'
  return quote.isStale ? '已过期' : '实时'
}

/** 根据报价可用性和新鲜度选择状态标签样式。 */
function quoteStatusType(quote: Quote): 'success' | 'warning' | 'danger' {
  if (quote.lastPrice === null || quote.lastPrice === undefined) return 'danger'
  return quote.isStale ? 'warning' : 'success'
}
</script>

<template>
  <span v-if="quote" class="quote-inline">
    <span class="quote-value-row">
      <strong>{{ formatMoney(quote.lastPrice, quote.currency ?? props.currency, 3) }}</strong>
      <span class="quote-currency">{{ quote.currency ?? props.currency ?? '币种未知' }}</span>
      <span :class="['quote-change', trendClass(quote.changePct)]">{{ formatSignedPercent(quote.changePct) }}</span>
      <el-tag :type="quoteStatusType(quote)" size="small">{{ quoteStatusLabel(quote) }}</el-tag>
    </span>
    <span class="quote-meta">
      <span v-if="quote.provider">{{ quote.provider }}</span>
      <span v-if="quote.sourceTime">报价 {{ formatDateTime(quote.sourceTime) }}</span>
      <span v-else>报价时间未知</span>
      <span v-if="quote.fetchedAt">获取 {{ formatDateTime(quote.fetchedAt) }}</span>
    </span>
  </span>
  <span v-else class="unavailable">行情不可用</span>
</template>
