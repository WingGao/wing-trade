<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Currency, Lot, Quote } from '../api/types'
import { calculateBreakevenSell, type BreakevenSellStrategy } from '../utils/breakevenSell'
import {
  formatDateTime,
  formatDecimal,
  formatMoney,
  formatQuantity,
  formatSignedMoney,
  formatSignedPercent,
  priceDigits,
  quantityUnit,
  trendClass,
} from '../utils/format'
import { useViewport } from '../utils/viewport'

const props = defineProps<{
  lots: Lot[]
  quote?: Quote | null
  currency: Currency
  isFund?: boolean
  symbolLabel: string
  securityName?: string | null
}>()

const visible = defineModel<boolean>({ default: false })
const { isMobile } = useViewport()

const simPrice = ref('')
const strategy = ref<BreakevenSellStrategy>('HIGHEST_LOSS_FIRST')

const isUS = computed(() => props.currency === 'USD')
const allowDecimalQty = computed(() => Boolean(props.isFund) || isUS.value)
const digits = computed(() => priceDigits(props.isFund, 3))
const unitLabel = computed(() => quantityUnit(props.isFund))
const qtyTarget = computed(() => ({
  currency: props.currency,
  securityType: props.isFund ? 'FUND' : 'STOCK',
}))

/** 格式化最新报价为模拟卖出价输入框字符串，去除多余尾零。 */
function formatInputPrice(raw: string | null | undefined): string {
  if (!raw) return ''
  const num = Number(raw)
  if (!Number.isFinite(num) || num <= 0) return ''
  return num.toFixed(digits.value).replace(/(\.\d*?[1-9])0+$/, '$1').replace(/\.0+$/, '')
}

/** 将模拟卖出价重置为当前最新行情价。 */
function resetToLatestPrice(): void {
  simPrice.value = formatInputPrice(props.quote?.lastPrice)
}

watch(visible, (open) => {
  if (open) {
    resetToLatestPrice()
  }
})

/** 实时执行盈利批 + 亏损批回本卖出测算（默认 ±1% 盈亏率容差）。 */
const result = computed(() =>
  calculateBreakevenSell({
    lots: props.lots,
    price: simPrice.value,
    allowDecimalQty: allowDecimalQty.value,
    lotStep: 100,
    toleranceRate: 0.01,
    strategy: strategy.value,
  }),
)

/** 返回当前最新价文本，供界面对比参考。 */
const latestPriceText = computed(() =>
  props.quote?.lastPrice ? formatMoney(props.quote.lastPrice, props.currency, digits.value) : '暂无行情',
)

/** 返回批次盈亏分类对应的中文标签与样式类型。 */
function sideTagMeta(side: 'PROFIT' | 'LOSS' | 'FLAT'): { label: string; type: 'danger' | 'success' | 'info' } {
  if (side === 'PROFIT') return { label: '盈利批', type: 'danger' }
  if (side === 'LOSS') return { label: '亏损批', type: 'success' }
  return { label: '平本批', type: 'info' }
}

/** 返回紧凑格式的买入日期（YYYY-MM-DD），节省弹窗横向空间。 */
function shortDate(iso: string): string {
  const full = formatDateTime(iso)
  return full === '—' ? full : full.slice(0, 10)
}

/** 返回当前市场步长、策略与容差规则提示文案。 */
const ruleHintText = computed(() => {
  const strategyText = strategy.value === 'HIGHEST_LOSS_FIRST'
    ? '亏损批优先匹配最高成本价（降剩余均价）'
    : '亏损批优先匹配浅亏批（最大化可卖量）'
  const stepText = allowDecimalQty.value ? '支持最多 4 位小数' : 'A 股按 100 股步长'
  return `规则：盈亏率容差 ±1% · ${strategyText} · ${stepText}（不含卖出手续费）`
})
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="`回本卖出测算 · ${symbolLabel}${securityName ? ` ${securityName}` : ''}`"
    :width="isMobile ? '94vw' : '740px'"
    class="breakeven-dialog"
    destroy-on-close
  >
    <div class="be-price-bar">
      <div class="be-price-input-row">
        <label class="be-price-label" for="be-sim-price">模拟卖出价</label>
        <el-input
          id="be-sim-price"
          v-model="simPrice"
          type="number"
          inputmode="decimal"
          step="any"
          min="0"
          placeholder="输入模拟卖出价"
          class="be-price-input"
          :size="isMobile ? 'small' : 'default'"
        />
        <el-button
          :size="isMobile ? 'small' : 'default'"
          plain
          :disabled="!quote?.lastPrice"
          @click="resetToLatestPrice"
        >
          恢复现价
        </el-button>
      </div>
      <div class="be-strategy-row">
        <span class="be-price-label">匹配策略</span>
        <el-radio-group
          v-model="strategy"
          :size="isMobile ? 'small' : 'default'"
          aria-label="回本卖出匹配策略"
        >
          <el-radio-button value="HIGHEST_LOSS_FIRST">优先消高价</el-radio-button>
          <el-radio-button value="MAX_QTY">优先多卖</el-radio-button>
        </el-radio-group>
      </div>
      <div class="be-price-meta muted">
        <span>当前价：<strong class="num">{{ latestPriceText }}</strong></span>
        <el-tag v-if="quote?.isStale" size="small" type="warning" effect="plain">行情已过期</el-tag>
        <span v-if="result.overallReturnPct !== null">
          全仓浮盈率：
          <strong :class="['num', trendClass(result.overallReturnPct)]">
            {{ formatSignedPercent(result.overallReturnPct) }}
          </strong>
        </span>
      </div>
      <p class="be-rule-hint muted">{{ ruleHintText }}</p>
    </div>

    <el-alert
      v-if="result.status === 'NO_OPEN_LOTS'"
      type="info"
      :closable="false"
      show-icon
      title="当前没有持有中的批次，无法进行回本测算。"
    />
    <el-alert
      v-else-if="result.status === 'INVALID_PRICE'"
      type="warning"
      :closable="false"
      show-icon
      title="暂无有效行情，请在上方输入大于 0 的模拟卖出价以开始测算。"
    />
    <el-alert
      v-else-if="result.status === 'NO_LOSS_LOTS'"
      type="success"
      :closable="false"
      show-icon
      :title="`当前模拟价下全部持仓均为盈利批（整体浮盈 ${formatSignedPercent(result.overallReturnPct)}），无需通过亏损批对冲回本。`"
    />
    <el-alert
      v-else-if="result.status === 'NO_PROFIT_LOTS'"
      type="warning"
      :closable="false"
      show-icon
      :title="`当前模拟价下全部持仓均为亏损批（整体浮亏 ${formatSignedPercent(result.overallReturnPct)}），缺少盈利批次可对冲回本。`"
    />
    <el-alert
      v-else-if="result.status === 'NO_SOLUTION'"
      type="warning"
      :closable="false"
      show-icon
      title="在当前模拟价与交易步长约束下，暂无满足 ±1% 盈亏率容差的盈利批 + 亏损批组合，可尝试微调模拟卖出价。"
    />

    <template v-else>
      <dl class="be-summary-grid" aria-label="回本卖出测算汇总">
        <div class="be-summary-card">
          <dt>建议回本卖出</dt>
          <dd class="be-summary-val num">
            {{ formatQuantity(result.sellQty, qtyTarget) }} {{ unitLabel }}
          </dd>
          <p class="be-summary-sub num">
            占总仓 {{ formatDecimal(result.sellRatioPct, 1) }}% · 盈 {{ formatQuantity(result.profitSellQty, qtyTarget) }} / 亏 {{ formatQuantity(result.lossSellQty, qtyTarget) }}
          </p>
        </div>
        <div class="be-summary-card">
          <dt>预计卖出金额</dt>
          <dd class="be-summary-val num">{{ formatMoney(result.sellAmount, currency) }}</dd>
          <p class="be-summary-sub num">对应成本 {{ formatMoney(result.allocatedCost, currency) }}</p>
        </div>
        <div class="be-summary-card">
          <dt>预计组合盈亏</dt>
          <dd :class="['be-summary-val', 'num', trendClass(result.estimatedPnl)]">
            {{ formatSignedMoney(result.estimatedPnl, currency) }}
          </dd>
          <p class="be-summary-sub num">
            盈亏率
            <strong :class="trendClass(result.estimatedReturnPct)">
              {{ formatSignedPercent(result.estimatedReturnPct) }}
            </strong>
            （±1% 内）
          </p>
        </div>
        <div class="be-summary-card">
          <dt>卖出后预计剩余</dt>
          <dd class="be-summary-val num">
            {{ formatQuantity(result.remainingQtyAfter, qtyTarget) }} {{ unitLabel }}
          </dd>
          <p class="be-summary-sub num">
            剩余均价 {{ result.remainingAvgCostAfter ? formatMoney(result.remainingAvgCostAfter, currency, digits) : '已清仓' }}
          </p>
        </div>
      </dl>

      <div class="be-list-section">
        <div class="be-list-head">
          <span>建议配对批次明细（共 {{ result.allocations.length }} 批）</span>
          <span class="muted">仅作测算参考，不会执行卖出记账</span>
        </div>

        <el-table
          v-if="!isMobile"
          :data="result.allocations"
          size="small"
          border
          max-height="320"
          class="be-table"
        >
          <el-table-column label="买入日期" width="102">
            <template #default="{ row }">
              <span class="num">{{ shortDate(row.executedAt) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="76" align="center">
            <template #default="{ row }">
              <el-tag :type="sideTagMeta(row.side).type" size="small" effect="plain">
                {{ sideTagMeta(row.side).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="成本单价" min-width="96" align="right">
            <template #default="{ row }">
              <span class="num">{{ formatMoney(row.unitCost, currency, digits) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="`剩余(${unitLabel})`" min-width="88" align="right">
            <template #default="{ row }">
              <span class="num">{{ formatQuantity(row.remainingQty, qtyTarget) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="`建议卖出(${unitLabel})`" min-width="116" align="right">
            <template #default="{ row }">
              <span class="num be-sell-qty">{{ formatQuantity(row.sellQty, qtyTarget) }}</span>
              <el-tag v-if="row.isPartial" size="small" type="info" effect="plain" class="be-partial-tag">部分</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="预计盈亏" min-width="136" align="right">
            <template #default="{ row }">
              <span :class="['num', trendClass(row.estimatedPnl)]">
                {{ formatSignedMoney(row.estimatedPnl, currency) }}
              </span>
              <span :class="['num', 'be-row-pct', trendClass(row.estimatedReturnPct)]">
                {{ formatSignedPercent(row.estimatedReturnPct) }}
              </span>
            </template>
          </el-table-column>
        </el-table>

        <div v-else class="m-be-list">
          <div v-for="row in result.allocations" :key="row.lotId" class="m-be-card">
            <div class="m-be-row">
              <div class="m-be-left">
                <el-tag :type="sideTagMeta(row.side).type" size="small" effect="plain">
                  {{ sideTagMeta(row.side).label }}
                </el-tag>
                <span class="num muted">{{ shortDate(row.executedAt) }}</span>
              </div>
              <div class="m-be-right num">
                卖 <strong>{{ formatQuantity(row.sellQty, qtyTarget) }}</strong> / {{ formatQuantity(row.remainingQty, qtyTarget) }} {{ unitLabel }}
                <span v-if="row.isPartial" class="m-be-partial">部分</span>
              </div>
            </div>
            <div class="m-be-row m-be-sub">
              <span class="num muted">成本 {{ formatMoney(row.unitCost, currency, digits) }}</span>
              <span :class="['num', trendClass(row.estimatedPnl)]">
                {{ formatSignedMoney(row.estimatedPnl, currency) }} ({{ formatSignedPercent(row.estimatedReturnPct) }})
              </span>
            </div>
          </div>
        </div>
      </div>
    </template>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.be-price-bar {
  margin-bottom: 14px;
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
}
.be-price-input-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.be-price-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}
.be-price-input {
  width: 180px;
  max-width: 100%;
}
.be-strategy-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.be-price-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 8px;
  font-size: 12px;
}
.be-rule-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.45;
}
.be-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 0 0 14px;
}
.be-summary-card {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}
.be-summary-card dt {
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.be-summary-val {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.be-summary-sub {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}
.be-list-section {
  min-width: 0;
}
.be-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
}
.be-list-head .muted {
  font-size: 12px;
  font-weight: 400;
}
.be-sell-qty {
  font-weight: 600;
}
.be-partial-tag {
  margin-left: 4px;
}
.be-row-pct {
  margin-left: 6px;
  font-size: 12px;
}
.m-be-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 46vh;
  overflow-y: auto;
}
.m-be-card {
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-bg-color);
}
.m-be-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}
.m-be-left {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.m-be-right {
  text-align: right;
  white-space: nowrap;
}
.m-be-partial {
  margin-left: 4px;
  padding: 0 4px;
  border-radius: 3px;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  font-size: 10px;
}
.m-be-sub {
  margin-top: 6px;
  font-size: 11px;
}
@media (max-width: 768px) {
  .be-price-input {
    flex: 1 1 120px;
    width: auto;
  }
  .be-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  .be-summary-card {
    padding: 8px 10px;
  }
  .be-summary-val {
    font-size: 14px;
  }
}
</style>
