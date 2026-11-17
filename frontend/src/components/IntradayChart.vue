<script setup lang="ts">
import { computed } from 'vue'
import type { IntradayRead } from '../api/types'
import { formatDecimal } from '../utils/format'

const props = defineProps<{ data: IntradayRead | null }>()

/** 仅将真实服务端时序映射为绘图坐标，不生成行情或参与账务计算。 */
const plot = computed(() => {
  const points = (props.data?.points ?? []).map((point) => ({ time: point.time, stamp: new Date(point.time).getTime(), price: Number(point.price) })).filter((point) => Number.isFinite(point.stamp) && Number.isFinite(point.price) && point.price > 0).sort((a, b) => a.stamp - b.stamp)
  if (!points.length) return null
  const prices = points.map((point) => point.price)
  const low = Math.min(...prices)
  const high = Math.max(...prices)
  const padding = Math.max((high - low) * 0.15, high * 0.0005)
  const min = low - padding
  const max = high + padding
  const first = points[0]!
  const last = points[points.length - 1]!
  const coordinates = points.map((point) => ({ x: 78 + (point.stamp - first.stamp) / Math.max(last.stamp - first.stamp, 1) * 795, y: 218 - (point.price - min) / (max - min) * 195 }))
  return { coordinates, line: coordinates.map((point) => `${point.x},${point.y}`).join(' '), ticks: [max, (max + min) / 2, min].map((value, index) => ({ label: formatDecimal(String(value), 3), y: 23 + index * 97.5 })), times: (last.stamp === first.stamp ? [0] : [0, 0.5, 1]).map((ratio) => ({ label: new Date(first.stamp + (last.stamp - first.stamp) * ratio).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }), x: 78 + ratio * 795 })) }
})
</script>

<template>
  <div class="intraday-chart">
    <svg v-if="plot" viewBox="0 0 900 260" role="img" aria-label="当前交易日股价走势图">
      <g v-for="tick in plot.ticks" :key="tick.y"><line x1="78" x2="873" :y1="tick.y" :y2="tick.y" stroke="#e8edf3" /><text x="66" :y="tick.y + 4" text-anchor="end">{{ tick.label }}</text></g>
      <polyline :points="plot.line" fill="none" stroke="#409eff" stroke-width="2" stroke-linejoin="round" />
      <circle v-if="plot.coordinates.length === 1" :cx="plot.coordinates[0]!.x" :cy="plot.coordinates[0]!.y" r="3" fill="#409eff" />
      <text v-for="(tick, index) in plot.times" :key="index" :x="tick.x" y="247" :text-anchor="index === 0 ? 'start' : index === 2 ? 'end' : 'middle'">{{ tick.label }}</text>
    </svg>
    <div v-else class="empty">暂无当日分时数据</div>
  </div>
</template>

<style scoped>
.intraday-chart { min-height: 200px; }
svg { display: block; width: 100%; max-height: 300px; }
text { fill: #8290a3; font-size: 12px; font-family: inherit; }
.empty { min-height: 200px; display: grid; place-items: center; color: #8290a3; }
</style>
