<script setup lang="ts">
import { computed, ref } from 'vue'
import type { IntradayRead } from '../api/types'
import { formatBeijingAxisTime, formatDecimal, formatMoney, formatSignedPercent, trendClass } from '../utils/format'

type Trend = 'up' | 'down' | 'neutral'

interface PlotPoint {
  time: string
  stamp: number
  price: number
  rawPrice: string
  x: number
  y: number
  timeLabel: string
  priceLabel: string
  changePctLabel: string | null
  changeTrend: Trend
}

const VIEWBOX_WIDTH = 900
const VIEWBOX_HEIGHT = 260
const PLOT_LEFT = 78
const PLOT_RIGHT = 824
const PLOT_WIDTH = PLOT_RIGHT - PLOT_LEFT
const PLOT_TOP = 23
const PLOT_BOTTOM = 218
const PLOT_HEIGHT = PLOT_BOTTOM - PLOT_TOP

const props = defineProps<{ data: IntradayRead | null }>()
const isNavSeries = computed(() => props.data?.seriesKind === 'nav')
const isEstimatedIntraday = computed(() => props.data?.seriesKind === 'estimated_intraday')
const containerRef = ref<HTMLDivElement | null>(null)
const svgRef = ref<SVGSVGElement | null>(null)
const hoverSvgX = ref<number | null>(null)

/** 按曲线类型格式化横轴标签（北京时间）：分时显示时分，基金近期净值显示月日。 */
function formatAxisTime(stamp: number): string {
  return formatBeijingAxisTime(stamp, isNavSeries.value ? 'date-short' : 'time')
}

/** 按曲线类型格式化悬浮提示时间（北京时间）：分时显示时分，基金净值显示年月日。 */
function formatTooltipTime(stamp: number): string {
  return formatBeijingAxisTime(stamp, isNavSeries.value ? 'date-full' : 'time')
}

/** 计算相对基准价的涨跌幅展示文本与涨跌样式。 */
function formatRelativeChange(price: number, basePrice: number | null): { label: string | null; trend: Trend } {
  if (basePrice === null || !Number.isFinite(basePrice) || basePrice <= 0) {
    return { label: null, trend: 'neutral' }
  }
  const pct = ((price - basePrice) / basePrice) * 100
  if (!Number.isFinite(pct)) return { label: null, trend: 'neutral' }
  const fixed = pct.toFixed(2)
  return {
    label: formatSignedPercent(fixed),
    trend: trendClass(fixed),
  }
}

/** 解析 0% 基准价：分时图优先使用昨收价，缺失或净值曲线时回退至首点价格。 */
function resolveBasePrice(firstPrice: number): number | null {
  if (!isNavSeries.value) {
    const prevClose = Number(props.data?.quote?.prevClose)
    if (Number.isFinite(prevClose) && prevClose > 0) return prevClose
  }
  return Number.isFinite(firstPrice) && firstPrice > 0 ? firstPrice : null
}

/** 使用二分查找按横坐标定位距离鼠标最近的数据点。 */
function findNearestPoint(points: PlotPoint[], targetX: number): PlotPoint | null {
  if (!points.length) return null
  if (points.length === 1 || targetX <= points[0]!.x) return points[0]!
  const lastIndex = points.length - 1
  if (targetX >= points[lastIndex]!.x) return points[lastIndex]!
  let left = 0
  let right = lastIndex
  while (left < right) {
    const mid = Math.floor((left + right) / 2)
    if (points[mid]!.x < targetX) left = mid + 1
    else right = mid
  }
  const prev = points[left - 1]!
  const next = points[left]!
  return Math.abs(targetX - prev.x) <= Math.abs(next.x - targetX) ? prev : next
}

/** 将指针事件的视口横坐标转换为 SVG viewBox 内的横坐标。 */
function resolveSvgX(event: PointerEvent): number | null {
  const svg = svgRef.value
  if (!svg) return null
  const ctm = svg.getScreenCTM()
  if (ctm && ctm.a !== 0) {
    return (event.clientX - ctm.e) / ctm.a
  }
  const rect = svg.getBoundingClientRect()
  if (!rect.width) return null
  return ((event.clientX - rect.left) / rect.width) * VIEWBOX_WIDTH
}

/** 将 SVG viewBox 坐标换算为图表容器内的定位样式，避免悬浮提示溢出边界。 */
function buildTooltipStyle(point: PlotPoint): Record<string, string> {
  const alignLeft = point.x > PLOT_LEFT + PLOT_WIDTH * 0.65
  const transform = alignLeft ? 'translate(calc(-100% - 12px), -50%)' : 'translate(12px, -50%)'
  const svg = svgRef.value
  const container = containerRef.value
  const ctm = svg?.getScreenCTM()
  if (svg && container && ctm) {
    const containerRect = container.getBoundingClientRect()
    const pixelX = point.x * ctm.a + ctm.e - containerRect.left
    const rawPixelY = point.y * ctm.d + ctm.f - containerRect.top
    const minY = 26
    const maxY = Math.max(minY, containerRect.height - 26)
    const clampedY = Math.min(Math.max(rawPixelY, minY), maxY)
    return {
      left: `${pixelX}px`,
      top: `${clampedY}px`,
      transform,
    }
  }
  return {
    left: `${(point.x / VIEWBOX_WIDTH) * 100}%`,
    top: `${(point.y / VIEWBOX_HEIGHT) * 100}%`,
    transform,
  }
}

/** 响应图表区域内的指针移动或点按，无需精确悬停在数据点上即可吸附最近点。 */
function handlePointerMove(event: PointerEvent): void {
  if (!plot.value) return
  const svgX = resolveSvgX(event)
  if (svgX === null) return
  hoverSvgX.value = svgX
}

/** 指针离开图表区域时清除十字准星与悬浮提示。 */
function handlePointerLeave(): void {
  hoverSvgX.value = null
}

/** 仅将真实服务端时序映射为绘图坐标，不生成行情或参与账务计算。 */
const plot = computed(() => {
  const points = (props.data?.points ?? [])
    .map((point) => ({ time: point.time, stamp: new Date(point.time).getTime(), price: Number(point.price), rawPrice: point.price }))
    .filter((point) => Number.isFinite(point.stamp) && Number.isFinite(point.price) && point.price > 0)
    .sort((a, b) => a.stamp - b.stamp)
  if (!points.length) return null
  const first = points[0]!
  const last = points[points.length - 1]!
  const basePrice = resolveBasePrice(first.price)
  const prices = points.map((point) => point.price)
  const low = basePrice !== null ? Math.min(...prices, basePrice) : Math.min(...prices)
  const high = basePrice !== null ? Math.max(...prices, basePrice) : Math.max(...prices)
  const padding = Math.max((high - low) * 0.15, high * 0.0005)
  const min = low - padding
  const max = high + padding
  const digits = (isNavSeries.value || isEstimatedIntraday.value) ? 4 : 3
  const currency = props.data?.currency
  const baselineY = basePrice !== null
    ? PLOT_BOTTOM - ((basePrice - min) / (max - min)) * PLOT_HEIGHT
    : null
  const coordinates: PlotPoint[] = points.map((point) => {
    const change = formatRelativeChange(point.price, basePrice)
    return {
      time: point.time,
      stamp: point.stamp,
      price: point.price,
      rawPrice: point.rawPrice,
      x: PLOT_LEFT + ((point.stamp - first.stamp) / Math.max(last.stamp - first.stamp, 1)) * PLOT_WIDTH,
      y: PLOT_BOTTOM - ((point.price - min) / (max - min)) * PLOT_HEIGHT,
      timeLabel: formatTooltipTime(point.stamp),
      priceLabel: currency ? formatMoney(point.rawPrice, currency, digits) : formatDecimal(point.rawPrice, digits),
      changePctLabel: change.label,
      changeTrend: change.trend,
    }
  })
  const ticks = [max, (max + min) / 2, min].map((value, index) => {
    const y = PLOT_TOP + index * (PLOT_HEIGHT / 2)
    const change = formatRelativeChange(value, basePrice)
    const nearBaseline = baselineY !== null && Math.abs(y - baselineY) < 15
    return {
      label: formatDecimal(String(value), digits),
      pctLabel: nearBaseline ? null : change.label,
      pctTrend: change.trend,
      y,
    }
  })
  return {
    coordinates,
    line: coordinates.map((point) => `${point.x},${point.y}`).join(' '),
    baseline: baselineY !== null ? { y: baselineY, label: '0.00%' } : null,
    ticks,
    times: (last.stamp === first.stamp ? [0] : [0, 0.5, 1]).map((ratio) => ({
      label: formatAxisTime(first.stamp + (last.stamp - first.stamp) * ratio),
      x: PLOT_LEFT + ratio * PLOT_WIDTH,
    })),
  }
})

/** 当前指针吸附高亮的数据点。 */
const activePoint = computed<PlotPoint | null>(() => {
  if (hoverSvgX.value === null || !plot.value) return null
  return findNearestPoint(plot.value.coordinates, hoverSvgX.value)
})

/** 当前悬浮提示卡片的定位样式。 */
const tooltipStyle = computed<Record<string, string>>(() => {
  if (!activePoint.value) return {}
  return buildTooltipStyle(activePoint.value)
})
</script>

<template>
  <div ref="containerRef" class="intraday-chart">
    <svg
      v-if="plot"
      ref="svgRef"
      viewBox="0 0 900 260"
      role="img"
      :aria-label="isNavSeries ? '近期单位净值走势图' : isEstimatedIntraday ? '当日盘中估算净值走势图' : '当前交易日股价走势图'"
      @pointermove="handlePointerMove"
      @pointerdown="handlePointerMove"
      @pointerleave="handlePointerLeave"
      @pointercancel="handlePointerLeave"
    >
      <g v-for="tick in plot.ticks" :key="tick.y">
        <line :x1="PLOT_LEFT" :x2="PLOT_RIGHT" :y1="tick.y" :y2="tick.y" stroke="#e8edf3" />
        <text x="68" :y="tick.y + 4" text-anchor="end">{{ tick.label }}</text>
        <text
          v-if="tick.pctLabel"
          :x="PLOT_RIGHT + 10"
          :y="tick.y + 4"
          text-anchor="start"
          :class="['pct-tick', tick.pctTrend]"
        >{{ tick.pctLabel }}</text>
      </g>
      <g v-if="plot.baseline" class="baseline">
        <line
          :x1="PLOT_LEFT"
          :x2="PLOT_RIGHT"
          :y1="plot.baseline.y"
          :y2="plot.baseline.y"
          stroke="#94a3b8"
          stroke-width="1.2"
          stroke-dasharray="4 3"
        />
        <text
          :x="PLOT_RIGHT + 10"
          :y="plot.baseline.y + 4"
          text-anchor="start"
          class="baseline-label"
        >{{ plot.baseline.label }}</text>
      </g>
      <polyline :points="plot.line" fill="none" stroke="#409eff" stroke-width="2" stroke-linejoin="round" />
      <circle v-if="plot.coordinates.length === 1" :cx="plot.coordinates[0]!.x" :cy="plot.coordinates[0]!.y" r="3" fill="#409eff" />
      <g v-if="activePoint" class="crosshair">
        <line :x1="activePoint.x" :x2="activePoint.x" :y1="PLOT_TOP" :y2="PLOT_BOTTOM" stroke="#94a3b8" stroke-width="1" stroke-dasharray="3 3" />
        <line :x1="PLOT_LEFT" :x2="PLOT_RIGHT" :y1="activePoint.y" :y2="activePoint.y" stroke="#cbd5e1" stroke-width="1" stroke-dasharray="3 3" />
        <circle :cx="activePoint.x" :cy="activePoint.y" r="7" fill="rgba(64, 158, 255, 0.2)" />
        <circle :cx="activePoint.x" :cy="activePoint.y" r="4" fill="#409eff" stroke="#ffffff" stroke-width="2" />
      </g>
      <text
        v-for="(tick, index) in plot.times"
        :key="index"
        :x="tick.x"
        y="247"
        :text-anchor="index === 0 ? 'start' : index === 2 ? 'end' : 'middle'"
      >{{ tick.label }}</text>
    </svg>
    <div v-if="activePoint" class="chart-tooltip" :style="tooltipStyle" role="status" aria-live="polite">
      <div class="chart-tooltip-time">{{ activePoint.timeLabel }}</div>
      <div class="chart-tooltip-row">
        <span class="chart-tooltip-price">{{ activePoint.priceLabel }}</span>
        <span v-if="activePoint.changePctLabel" :class="['chart-tooltip-pct', activePoint.changeTrend]">
          {{ activePoint.changePctLabel }}
        </span>
      </div>
    </div>
    <div v-if="!plot" class="empty">{{ isNavSeries ? '暂无近期净值数据' : '暂无当日分时数据' }}</div>
  </div>
</template>

<style scoped>
.intraday-chart { position: relative; min-height: 200px; }
svg { display: block; width: 100%; max-height: 300px; cursor: crosshair; user-select: none; }
text { fill: #8290a3; font-size: 12px; font-family: inherit; }
.pct-tick.up { fill: #e34d3c; }
.pct-tick.down { fill: #199868; }
.pct-tick.neutral { fill: #657287; }
.baseline { pointer-events: none; }
.baseline-label { fill: #475569; font-weight: 600; }
.crosshair { pointer-events: none; }
.chart-tooltip {
  position: absolute;
  z-index: 5;
  pointer-events: none;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid #dbe2ec;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 4px 12px rgba(24, 34, 48, 0.12);
  white-space: nowrap;
  line-height: 1.35;
}
.chart-tooltip-time { font-size: 11px; color: #64748b; font-variant-numeric: tabular-nums; }
.chart-tooltip-row { display: flex; align-items: baseline; gap: 8px; }
.chart-tooltip-price { font-size: 13px; font-weight: 600; color: #182230; font-variant-numeric: tabular-nums; }
.chart-tooltip-pct { font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
.chart-tooltip-pct.up { color: #e34d3c; }
.chart-tooltip-pct.down { color: #199868; }
.chart-tooltip-pct.neutral { color: #657287; }
.empty { min-height: 200px; display: grid; place-items: center; color: #8290a3; }
@media (max-width: 768px) {
  .intraday-chart { min-height: 160px; }
  svg { max-height: 200px; }
  .empty { min-height: 160px; font-size: 12px; }
}
</style>
