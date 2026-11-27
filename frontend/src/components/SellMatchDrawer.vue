<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Currency, Lot, SellPreview } from '../api/types'
import { useWatchlistStore } from '../stores/watchlist'
import { formatDateTime, formatMoney, formatQuantity, priceDigits, quantityUnit, trendClass } from '../utils/format'
import {
  buildManualSelectionsFromMatches,
  buildManualSelectionsFromPreview,
  filterSellableLots,
  isLotAfterSellTime,
  normalizeQtyString,
  resolveSellLotPrice,
  serializeManualSelectionsKey,
  summarizeManualSelections,
  validateAndBuildManualMatches,
  type ManualLotSelection,
  type ManualSellMatchItem,
  type SellMatchMode,
} from '../utils/manualSellMatch'
import { nowBeijingPickerValue, toAwareBeijingIso } from '../utils/time'
import { useViewport } from '../utils/viewport'

interface PreviewInput {
  accountId?: string
  securityId: string
  qty: string
  price: string
  executedAt: string
  commission: string
  otherFee: string
  mode: SellMatchMode
  manualKey: string
  key: string
}

const props = defineProps<{
  securityId: string
  lots: Lot[]
  accountId?: string
  currency?: Currency
  isFund?: boolean
  initialQty?: string
  initialMatches?: ManualSellMatchItem[]
}>()
const emit = defineEmits<{ saved: [] }>()
const visible = defineModel<boolean>({ default: false })
const watchlistStore = useWatchlistStore()
const { isMobile } = useViewport()
const isUS = computed(() => props.currency === 'USD')
const allowDecimalQty = computed(() => Boolean(props.isFund) || isUS.value)
const qtyTarget = computed(() => ({ currency: props.currency, securityType: props.isFund ? 'FUND' : 'STOCK' }))
const form = reactive({ qty: '', price: '', executedAt: nowBeijingPickerValue(), commission: '', otherFee: '' })
const matchMode = ref<SellMatchMode>('EQUAL')
const manualSelections = ref<Record<string, ManualLotSelection>>({})
const watchAfterClear = ref(false)
const watchTargetBuyPrice = ref('')
const watchAlertEnabled = ref(false)
const preview = ref<SellPreview | null>(null)
const previewInput = ref<PreviewInput | null>(null)
const loading = ref(false)
const submitting = ref(false)
let previewGeneration = 0

/** 筛选当前证券可卖出的未结清批次（按买入时间倒序）。 */
const sellableLots = computed(() => filterSellableLots(props.lots))

/** 计算当前证券可卖出的未结清总数量。 */
const totalAvailableQty = computed(() =>
  sellableLots.value.reduce((sum, lot) => sum + (Number(lot.remainingQty) || 0), 0),
)

/** 实时汇总手动关联模式下已选批次数、已分配总量及与目标卖出量的差额。 */
const manualSummary = computed(() =>
  summarizeManualSelections(sellableLots.value, manualSelections.value, form.qty),
)

/** 判断本次预览是否将清空全部持仓且该证券当前尚未关注。 */
const showClearWatchOption = computed(() => {
  if (!preview.value || totalAvailableQty.value <= 0) return false
  if (watchlistStore.findBySecurityId(props.securityId)) return false
  const previewQty = Number(preview.value.qty)
  return Number.isFinite(previewQty) && previewQty >= totalAvailableQty.value - 1e-6
})

const hasValidWatchPrice = computed(() => {
  const raw = watchTargetBuyPrice.value.trim()
  if (!raw) return false
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0
})

/** 将表单时间安全转换为带 +08:00 时区的北京时间 ISO 字符串。 */
function executedAtIso(): string {
  try {
    return toAwareBeijingIso(form.executedAt)
  } catch {
    return ''
  }
}

/** 为当前账户、证券、模式与卖出输入生成不可变预览快照。 */
function buildPreviewInput(): PreviewInput {
  const accountId = props.accountId
  const mode = matchMode.value
  const manualKey = mode === 'MANUAL' ? serializeManualSelectionsKey(sellableLots.value, manualSelections.value) : ''
  const input = {
    accountId,
    securityId: props.securityId,
    qty: form.qty.trim(),
    price: form.price.trim(),
    executedAt: executedAtIso(),
    commission: form.commission.trim() || '0',
    otherFee: form.otherFee.trim() || '0',
    mode,
    manualKey,
  }
  return {
    ...input,
    key: JSON.stringify([
      input.accountId ?? '',
      input.securityId,
      input.qty,
      input.price,
      input.executedAt,
      input.commission,
      input.otherFee,
      input.mode,
      input.manualKey,
    ]),
  }
}

const currentPreviewKey = computed(() => buildPreviewInput().key)
const previewCurrency = computed<Currency | null>(() => preview.value?.currency ?? props.currency ?? null)
const canSubmit = computed(() => Boolean(preview.value && previewInput.value?.key === currentPreviewKey.value && !loading.value))

/** 清除旧预览并推进代次，使在途旧响应无法恢复确认状态。 */
function invalidatePreview() {
  previewGeneration += 1
  preview.value = null
  previewInput.value = null
  loading.value = false
}

/** 返回批次短编号，完整编号保留在提示中。 */
function shortLotId(lotId: string): string {
  return lotId.slice(0, 8)
}

/** 返回手动模式下指定批次是否已被勾选。 */
function isLotChecked(lotId: string): boolean {
  return Boolean(manualSelections.value[lotId]?.checked)
}

/** 返回手动模式下指定批次当前输入的配对数量文本。 */
function getLotInputQty(lotId: string): string {
  return manualSelections.value[lotId]?.qty ?? ''
}

/** 切换手动模式下单个批次的勾选状态，首次勾选默认填入其全部可用量。 */
function toggleManualLot(lot: Lot, checked: boolean): void {
  const current = manualSelections.value[lot.id]
  const nextQty = checked
    ? (current?.qty?.trim() ? current.qty : normalizeQtyString(lot.remainingQty))
    : (current?.qty ?? '')
  manualSelections.value = {
    ...manualSelections.value,
    [lot.id]: { checked, qty: nextQty },
  }
}

/** 更新手动模式下单个批次的配对数量，并在输入非空值时自动勾选。 */
function updateManualLotQty(lot: Lot, rawValue: string): void {
  const trimmed = rawValue.trim()
  manualSelections.value = {
    ...manualSelections.value,
    [lot.id]: {
      checked: trimmed.length > 0,
      qty: rawValue,
    },
  }
}

/** 将指定批次快速勾选并填满其全部可用数量。 */
function fillManualLotFull(lot: Lot): void {
  manualSelections.value = {
    ...manualSelections.value,
    [lot.id]: {
      checked: true,
      qty: normalizeQtyString(lot.remainingQty),
    },
  }
}

/** 清空当前手动模式下的全部批次勾选与配对数量。 */
function clearManualSelections(): void {
  manualSelections.value = {}
}

/** 将当前手动模式已分配的合法合计数量同步回填到上方卖出数量输入框。 */
function syncAllocatedQtyToForm(): void {
  if (manualSummary.value.allocatedScaled <= 0n) return
  form.qty = manualSummary.value.allocatedQty
}

/** 将当前自动等量配对预览结果一键带入手动关联模式以便微调。 */
function convertPreviewToManual(): void {
  if (!preview.value?.matches.length) return
  manualSelections.value = buildManualSelectionsFromPreview(sellableLots.value, preview.value.matches)
  matchMode.value = 'MANUAL'
}

/** 判断指定批次的买入时间是否晚于当前填写的卖出成交时间。 */
function isFutureLot(lot: Lot): boolean {
  return isLotAfterSellTime(lot.executedAt, executedAtIso())
}

watch(currentPreviewKey, invalidatePreview)
watch(visible, (open) => {
  if (open) {
    form.executedAt = nowBeijingPickerValue()
    watchAfterClear.value = false
    watchTargetBuyPrice.value = ''
    watchAlertEnabled.value = false
    if (props.initialMatches && props.initialMatches.length > 0) {
      const nextSelections = buildManualSelectionsFromMatches(sellableLots.value, props.initialMatches)
      manualSelections.value = nextSelections
      matchMode.value = 'MANUAL'
      const summary = summarizeManualSelections(sellableLots.value, nextSelections, '')
      form.qty = props.initialQty !== undefined
        ? normalizeQtyString(props.initialQty)
        : (summary.allocatedScaled > 0n ? summary.allocatedQty : '')
    } else {
      matchMode.value = 'EQUAL'
      manualSelections.value = {}
      if (props.initialQty !== undefined) {
        form.qty = normalizeQtyString(props.initialQty)
      }
    }
  } else {
    invalidatePreview()
  }
}, { immediate: true })

watch(watchTargetBuyPrice, (next) => {
  if (!next.trim() && watchAlertEnabled.value) {
    watchAlertEnabled.value = false
  }
})

/** 按当前模式请求服务端卖出预览，并丢弃已过期响应。 */
async function previewSell() {
  const input = buildPreviewInput()
  if (!input.qty || !input.price || !input.executedAt) {
    ElMessage.warning('请填写有效的卖出数量、价格和成交时间')
    return
  }
  let manualMatches: ManualSellMatchItem[] | undefined
  if (input.mode === 'MANUAL') {
    const validated = validateAndBuildManualMatches(
      sellableLots.value,
      manualSelections.value,
      input.qty,
      allowDecimalQty.value,
    )
    if (!validated.ok) {
      ElMessage.warning(validated.message)
      return
    }
    manualMatches = validated.matches
  }
  const generation = ++previewGeneration
  preview.value = null
  previewInput.value = null
  loading.value = true
  try {
    const payload: Record<string, unknown> = {
      accountId: input.accountId,
      securityId: input.securityId,
      qty: input.qty,
      price: input.price,
      executedAt: input.executedAt,
      commission: input.commission,
      otherFee: input.otherFee,
    }
    if (input.mode === 'MANUAL' && manualMatches) {
      payload.matches = manualMatches
    } else {
      payload.matchingMethod = 'EQUAL'
    }
    const result = await api.previewSell(payload)
    if (generation !== previewGeneration) return
    preview.value = result
    previewInput.value = input
  } catch (error) {
    if (generation === previewGeneration) ElMessage.error(error instanceof Error ? error.message : '预览失败')
  } finally {
    if (generation === previewGeneration) loading.value = false
  }
}

/** 使用当前预览的明确批次提交卖出，避免确认时重新选择批次。 */
async function submit() {
  if (!canSubmit.value || !preview.value || !previewInput.value) {
    ElMessage.warning('卖出输入已变化，请重新预览')
    return
  }
  if (submitting.value) return
  submitting.value = true
  try {
    await api.sell({
      accountId: previewInput.value.accountId,
      securityId: previewInput.value.securityId,
      qty: preview.value.qty,
      price: preview.value.price,
      executedAt: preview.value.executedAt,
      commission: previewInput.value.commission,
      otherFee: previewInput.value.otherFee,
      clientRequestId: crypto.randomUUID(),
      matches: preview.value.matches.map((match) => ({ lotId: match.lotId, matchedQty: match.matchedQty })),
    })
    if (showClearWatchOption.value && watchAfterClear.value) {
      const rawPrice = watchTargetBuyPrice.value.trim()
      try {
        await watchlistStore.add(
          {
            securityId: previewInput.value.securityId,
            accountId: previewInput.value.accountId,
            targetBuyPrice: hasValidWatchPrice.value ? rawPrice : null,
            alertEnabled: hasValidWatchPrice.value ? watchAlertEnabled.value : false,
          },
          previewInput.value.accountId,
        )
        ElMessage.success('卖出已记账，并已加入关注列表')
      } catch (watchErr) {
        ElMessage.warning(
          `卖出已记账，但加入关注失败：${watchErr instanceof Error ? watchErr.message : '请稍后手动关注'}`,
        )
      }
    } else {
      ElMessage.success('卖出已记账')
    }
    visible.value = false
    emit('saved')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '卖出失败')
  } finally {
    submitting.value = false
  }
}
</script>
<template>
  <el-drawer v-model="visible" :title="`卖出配对工作台（${previewCurrency ?? currency ?? '币种未知'}）`" :size="isMobile ? '100%' : 'min(760px, 96vw)'">
    <div class="mode-bar">
      <span class="mode-label">配对方式</span>
      <el-radio-group v-model="matchMode" size="small">
        <el-radio-button value="EQUAL">自动等量配对</el-radio-button>
        <el-radio-button value="MANUAL">手动关联批次</el-radio-button>
      </el-radio-group>
    </div>

    <el-form class="sell-form" :label-position="isMobile ? 'top' : 'right'" :label-width="isMobile ? undefined : '96px'">
      <el-form-item :label="isFund ? '赎回份额' : '卖出数量'"><el-input v-model="form.qty" :inputmode="allowDecimalQty ? 'decimal' : 'numeric'" :placeholder="isFund ? '支持小数份额' : isUS ? '支持碎股小数，如 0.5' : undefined" /></el-form-item>
      <el-form-item :label="isFund ? '单位净值' : '卖出价格'"><el-input v-model="form.price" inputmode="decimal" :placeholder="isFund ? '如 1.2345' : isUS ? '美元单价，如 235.00' : undefined" /></el-form-item>
      <el-form-item label="成交时间"><el-date-picker v-model="form.executedAt" type="datetime" value-format="YYYY-MM-DDTHH:mm" /></el-form-item>
      <el-form-item :label="isFund ? '赎回费' : '佣金'"><el-input v-model="form.commission" inputmode="decimal" placeholder="默认 0" /></el-form-item>
      <el-form-item v-if="!isFund" :label="isUS ? '平台/监管费' : '其他费用'"><el-input v-model="form.otherFee" inputmode="decimal" placeholder="默认 0" /></el-form-item>
    </el-form>

    <section v-if="matchMode === 'MANUAL'" class="manual-section">
      <div class="manual-toolbar">
        <div class="manual-stats num">
          <span>已选 <strong>{{ manualSummary.selectedCount }}</strong> 批</span>
          <span>已分配 <strong>{{ manualSummary.allocatedQty }}</strong> {{ quantityUnit(isFund) }}</span>
          <span v-if="manualSummary.targetQty">
            目标 <strong>{{ manualSummary.targetQty }}</strong>
            <em v-if="manualSummary.isExactMatch" class="manual-ok">（已匹配）</em>
            <em v-else-if="manualSummary.diffQty && manualSummary.diffScaled !== 0n" class="manual-warn">
              （{{ manualSummary.diffScaled! > 0n ? `多出 ${manualSummary.diffQty}` : `还差 ${manualSummary.diffQty.replace('-', '')}` }}）
            </em>
          </span>
        </div>
        <div class="manual-actions">
          <el-button size="small" :disabled="manualSummary.allocatedScaled <= 0n" @click="syncAllocatedQtyToForm">同步为卖出数量</el-button>
          <el-button size="small" :disabled="manualSummary.selectedCount === 0" @click="clearManualSelections">清空选择</el-button>
        </div>
      </div>

      <div v-if="!sellableLots.length" class="muted manual-empty">当前没有可卖出的未结清批次</div>
      <el-table v-else-if="!isMobile" :data="sellableLots" row-key="id" size="small" class="manual-table">
        <el-table-column width="42" align="center">
          <template #default="{ row }">
            <el-checkbox
              :model-value="isLotChecked(row.id)"
              @update:model-value="(val: unknown) => toggleManualLot(row, Boolean(val))"
            />
          </template>
        </el-table-column>
        <el-table-column label="买入时间 / 批次" min-width="165">
          <template #default="{ row }">
            <div class="manual-lot-cell">
              <strong class="match-primary num">{{ formatDateTime(row.executedAt) }}</strong>
              <div class="match-secondary">
                批次 <span class="lot-code" :title="row.id">{{ shortLotId(row.id) }}</span>
                <el-tag v-if="isFutureLot(row)" type="warning" size="small" class="future-tag">晚于卖出时间</el-tag>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="isFund ? '买入净值' : '买入价'" min-width="105">
          <template #default="{ row }">
            <strong class="match-primary num">{{ formatMoney(resolveSellLotPrice(row), currency, priceDigits(isFund, 3)) }}</strong>
          </template>
        </el-table-column>
        <el-table-column label="可用数量" min-width="95">
          <template #default="{ row }">
            <span class="num">{{ formatQuantity(row.remainingQty, qtyTarget) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="本次关联数量" min-width="190">
          <template #default="{ row }">
            <div class="manual-qty-cell">
              <el-input
                :model-value="getLotInputQty(row.id)"
                size="small"
                :inputmode="allowDecimalQty ? 'decimal' : 'numeric'"
                :placeholder="`最多 ${normalizeQtyString(row.remainingQty)}`"
                @update:model-value="(val: unknown) => updateManualLotQty(row, String(val))"
              />
              <el-button link type="primary" size="small" @click="fillManualLotFull(row)">填满</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="m-manual-list">
        <article v-for="row in sellableLots" :key="row.id" :class="['m-manual-item', { 'm-manual-item-checked': isLotChecked(row.id) }]">
          <div class="m-manual-head">
            <label class="m-manual-check">
              <el-checkbox
                :model-value="isLotChecked(row.id)"
                @update:model-value="(val: unknown) => toggleManualLot(row, Boolean(val))"
              />
              <span class="lot-code" :title="row.id">{{ shortLotId(row.id) }}</span>
              <span class="match-secondary num">{{ formatDateTime(row.executedAt) }}</span>
            </label>
            <el-button link type="primary" size="small" @click="fillManualLotFull(row)">填满</el-button>
          </div>
          <div class="m-manual-meta num">
            <span>{{ isFund ? '净值' : '买价' }} <strong>{{ formatMoney(resolveSellLotPrice(row), currency, priceDigits(isFund, 3)) }}</strong></span>
            <span>可用 <strong>{{ formatQuantity(row.remainingQty, qtyTarget) }}</strong> {{ quantityUnit(isFund) }}</span>
            <el-tag v-if="isFutureLot(row)" type="warning" size="small">晚于卖出时间</el-tag>
          </div>
          <div class="m-manual-input-row">
            <el-input
              :model-value="getLotInputQty(row.id)"
              size="small"
              :inputmode="allowDecimalQty ? 'decimal' : 'numeric'"
              :placeholder="`关联数量（最多 ${normalizeQtyString(row.remainingQty)}）`"
              @update:model-value="(val: unknown) => updateManualLotQty(row, String(val))"
            />
          </div>
        </article>
      </div>
    </section>

    <div class="preview-action-row">
      <el-button type="primary" :loading="loading" @click="previewSell">
        {{ matchMode === 'MANUAL' ? '预览手动关联配对' : '预览最近优先等量配对' }}
      </el-button>
    </div>
    <p v-if="matchMode === 'EQUAL'" class="muted mode-hint">优先最近买入批次，跨批次尽量等量扣减；容量不足或余量由服务端分配，差价计入已实现收益。</p>
    <p v-else class="muted mode-hint">手动指定卖出关联的买入批次及数量；如后续编辑历史成交并执行「重新计算」，系统仍按默认等量配对重放。</p>
    <el-divider />
    <div v-if="preview" class="preview">
      <div :class="['preview-summary', { 'm-preview-summary': isMobile }]">
        <div><span class="preview-label">预计净回款</span><strong class="num">{{ formatMoney(preview.cashIn, previewCurrency) }}</strong></div>
        <div><span class="preview-label">已实现收益</span><strong :class="['num', trendClass(preview.realizedPnl)]">{{ formatMoney(preview.realizedPnl, previewCurrency) }}</strong></div>
        <div><span class="preview-label">预览成交量</span><strong class="num">{{ formatQuantity(preview.qty, isFund) }}</strong></div>
      </div>
      <div v-if="matchMode === 'EQUAL' && preview.matches.length" class="convert-manual-bar">
        <span class="match-secondary">想微调本次配对的批次或数量？</span>
        <el-button link type="primary" size="small" @click="convertPreviewToManual">转为手动调整</el-button>
      </div>
      <div v-if="showClearWatchOption" class="clear-watch-box">
        <el-checkbox v-model="watchAfterClear">清仓后继续关注此标的</el-checkbox>
        <div v-if="watchAfterClear" class="clear-watch-fields">
          <el-input
            v-model="watchTargetBuyPrice"
            size="small"
            inputmode="decimal"
            clearable
            :placeholder="isFund ? '可选：目标买回净值' : '可选：目标买回价'"
            style="width: 180px"
          />
          <el-switch
            v-model="watchAlertEnabled"
            size="small"
            :disabled="!hasValidWatchPrice"
            active-text="企微到价提醒"
          />
        </div>
      </div>
      <el-table v-if="!isMobile" :data="preview.matches" row-key="lotId" size="small" class="match-table">
        <el-table-column label="买入时间" min-width="170">
          <template #default="{ row }"><strong class="match-primary">{{ formatDateTime(row.buyExecutedAt) }}</strong></template>
        </el-table-column>
        <el-table-column :label="isFund ? '原始净值' : '原始成交价'" min-width="135">
          <template #default="{ row }"><strong class="match-primary">{{ formatMoney(row.buyPrice, previewCurrency, priceDigits(isFund, 2)) }}</strong><div class="match-secondary">{{ previewCurrency ?? '币种未知' }}</div></template>
        </el-table-column>
        <el-table-column label="本次配对数量" min-width="230">
          <template #default="{ row }">
            <strong class="match-primary">{{ formatQuantity(row.matchedQty, isFund) }}</strong>
            <div class="match-secondary">原始买入 {{ formatQuantity(row.buyQty, isFund) }} · 预览前可用 {{ formatQuantity(row.availableQty, isFund) }}</div>
            <div class="match-secondary">批次 <span class="lot-code" :title="row.lotId">{{ shortLotId(row.lotId) }}</span></div>
          </template>
        </el-table-column>
        <el-table-column label="分摊成本" min-width="120">
          <template #default="{ row }">{{ formatMoney(row.allocatedBuyCost, previewCurrency) }}</template>
        </el-table-column>
      </el-table>
      <div v-else class="m-match-list">
        <article v-for="row in preview.matches" :key="row.lotId" class="m-match-item">
          <div class="m-match-top">
            <span class="lot-code" :title="row.lotId">{{ shortLotId(row.lotId) }}</span>
            <span class="match-secondary num">{{ formatDateTime(row.buyExecutedAt) }}</span>
          </div>
          <div class="m-match-grid">
            <div class="m-match-cell">
              <span class="preview-label">{{ isFund ? '原始净值' : '原始价' }}</span>
              <strong class="match-primary num">{{ formatMoney(row.buyPrice, previewCurrency, priceDigits(isFund, 2)) }}</strong>
            </div>
            <div class="m-match-cell">
              <span class="preview-label">本次配对</span>
              <strong class="match-primary num">{{ formatQuantity(row.matchedQty, isFund) }} {{ quantityUnit(isFund) }}</strong>
              <span class="match-secondary num">买 {{ formatQuantity(row.buyQty, isFund) }} / 用 {{ formatQuantity(row.availableQty, isFund) }}</span>
            </div>
            <div class="m-match-cell m-match-right">
              <span class="preview-label">分摊成本</span>
              <strong class="match-primary num">{{ formatMoney(row.allocatedBuyCost, previewCurrency) }}</strong>
            </div>
          </div>
        </article>
      </div>
    </div>
    <div v-else class="preview-placeholder muted">尚无有效预览</div>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="danger" :loading="submitting" :disabled="!canSubmit" @click="submit">确认卖出</el-button>
    </template>
  </el-drawer>
</template>
<style scoped>
.mode-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.mode-label { font-size: 13px; color: var(--el-text-color-regular); font-weight: 500; }
.sell-form { max-width: 520px; }
.sell-form :deep(.el-date-editor) { width: 100%; }
.manual-section { margin: 8px 0 14px; padding: 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; background: #fafbfd; }
.manual-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.manual-stats { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: 13px; color: var(--el-text-color-regular); }
.manual-ok { font-style: normal; color: #16a34a; font-weight: 600; }
.manual-warn { font-style: normal; color: #d97706; font-weight: 600; }
.manual-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.manual-empty { padding: 16px 0; text-align: center; font-size: 13px; }
.manual-table { width: 100%; }
.manual-lot-cell { display: flex; flex-direction: column; gap: 2px; line-height: 1.35; }
.future-tag { margin-left: 4px; }
.manual-qty-cell { display: flex; align-items: center; gap: 6px; }
.preview-action-row { margin-top: 4px; }
.mode-hint { margin: 8px 0 0; font-size: 12px; line-height: 1.5; }
.convert-manual-bar { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 10px; padding: 6px 10px; border-radius: 6px; background: #f8fafc; }
.preview-summary { display: flex; flex-wrap: wrap; gap: 20px 36px; padding: 14px 0; border-top: 1px solid var(--el-border-color-lighter); border-bottom: 1px solid var(--el-border-color-lighter); }
.preview-summary > div { display: flex; min-width: 140px; flex-direction: column; gap: 4px; }
.preview-label, .match-secondary { color: var(--el-text-color-secondary); font-size: 12px; }
.match-primary { color: var(--el-text-color-primary); }
.match-table { margin-top: 16px; }
.lot-code { border-bottom: 1px dotted currentColor; cursor: help; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.preview-placeholder { padding: 28px 0; text-align: center; }
.clear-watch-box { margin-top: 12px; padding: 10px 12px; border: 1px dashed var(--el-border-color); border-radius: 6px; background: #fafcff; }
.clear-watch-fields { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 8px; }
@media (max-width: 600px) {
  .preview-summary { gap: 12px 20px; }
  .preview-summary > div { min-width: 120px; }
}
.m-manual-list { display: flex; flex-direction: column; gap: 8px; }
.m-manual-item { padding: 8px 10px; border: 1px solid #e5e9f2; border-radius: 6px; background: #fff; }
.m-manual-item-checked { border-color: #93c5fd; background: #f8fbff; }
.m-manual-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 4px; }
.m-manual-check { display: inline-flex; align-items: center; gap: 6px; min-width: 0; flex-wrap: wrap; font-size: 12px; cursor: pointer; }
.m-manual-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 12px; color: var(--el-text-color-regular); margin-bottom: 6px; }
.m-manual-input-row { width: 100%; }
.m-preview-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; padding: 10px 0; }
.m-preview-summary > div { min-width: 0; font-size: 13px; }
.m-match-list { margin-top: 10px; border: 1px solid #edf0f5; border-radius: 6px; overflow: hidden; }
.m-match-item { padding: 8px 10px; border-bottom: 1px solid #edf0f5; }
.m-match-item:last-child { border-bottom: 0; }
.m-match-top { display: flex; justify-content: space-between; align-items: center; gap: 6px; margin-bottom: 5px; font-size: 11px; }
.m-match-grid { display: grid; grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.2fr) minmax(0, 0.9fr); gap: 6px; }
.m-match-cell { display: flex; flex-direction: column; gap: 2px; min-width: 0; font-size: 12px; }
.m-match-right { text-align: right; }
</style>
