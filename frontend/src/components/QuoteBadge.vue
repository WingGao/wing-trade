<script setup lang="ts">
import type { Quote } from '../api/types'
defineProps<{ quote?: Quote | null }>()
</script>
<template>
  <span v-if="quote" class="quote-inline">
    <strong>{{ quote.lastPrice ?? '—' }}</strong>
    <span :class="['quote-change', Number(quote.changePct ?? 0) >= 0 ? 'up' : 'down']">{{ quote.changePct ? `${Number(quote.changePct) >= 0 ? '+' : ''}${quote.changePct}%` : '—' }}</span>
    <el-tag v-if="quote.isStale" size="small" type="warning">已过期</el-tag>
  </span>
  <span v-else class="muted">行情不可用</span>
</template>
