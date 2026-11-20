<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Currency, Lot, SellPreview } from '../api/types'
import { formatDateTime, formatMoney, formatQuantity, trendClass } from '../utils/format'
import { useViewport } from '../utils/viewport'

interface PreviewInput {
  accountId?: string
  securityId: string
  qty: string
  price: string
  executedAt: string
  key: string
}

const props = defineProps<{ securityId: string; lots: Lot[]; accountId?: string; currency?: Currency }>()
const emit = defineEmits<{ saved: [] }>()
const visible = defineModel<boolean>({ default: false })
const { isMobile } = useViewport()
const form = reactive({ qty: '', price: '', executedAt: new Date().toISOString().slice(0, 16) })
const preview = ref<SellPreview | null>(null)
const previewInput = ref<PreviewInput | null>(null)
const loading = ref(false)
const submitting = ref(false)
let previewGeneration = 0

/** 将表单时间安全转换为接口使用的 ISO 时间。 */
function executedAtIso(): string {
  const parsed = new Date(form.executedAt)
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toISOString()
}

/** 为当前账户、证券和卖出输入生成不可变预览快照。 */
function buildPreviewInput(): PreviewInput {
  const accountId = props.accountId
  const input = {
    accountId,
    securityId: props.securityId,
    qty: form.qty.trim(),
    price: form.price.trim(),
    executedAt: executedAtIso(),
  }
  return { ...input, key: JSON.stringify([input.accountId ?? '', input.securityId, input.qty, input.price, input.executedAt]) }
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

watch(() => [props.accountId, props.securityId, form.qty, form.price, form.executedAt], invalidatePreview)
watch(visible, (open) => { if (!open) invalidatePreview() })

/** 请求最近买入优先且跨批次等量分配的预览，并丢弃已过期响应。 */
async function previewSell() {
  const input = buildPreviewInput()
  if (!input.qty || !input.price || !input.executedAt) {
    ElMessage.warning('请填写有效的卖出数量、价格和成交时间')
    return
  }
  const generation = ++previewGeneration
  preview.value = null
  previewInput.value = null
  loading.value = true
  try {
    const result = await api.previewSell({
      accountId: input.accountId,
      securityId: input.securityId,
      qty: input.qty,
      price: input.price,
      executedAt: input.executedAt,
      matchingMethod: 'EQUAL',
    })
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
      clientRequestId: crypto.randomUUID(),
      matches: preview.value.matches.map((match) => ({ lotId: match.lotId, matchedQty: match.matchedQty })),
    })
    ElMessage.success('卖出已记账')
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
    <el-form class="sell-form" :label-position="isMobile ? 'top' : 'right'" :label-width="isMobile ? undefined : '86px'">
      <el-form-item label="卖出数量"><el-input v-model="form.qty" inputmode="numeric" /></el-form-item>
      <el-form-item label="卖出价格"><el-input v-model="form.price" inputmode="decimal" /></el-form-item>
      <el-form-item label="成交时间"><el-date-picker v-model="form.executedAt" type="datetime" value-format="YYYY-MM-DDTHH:mm" /></el-form-item>
    </el-form>
    <el-button type="primary" :loading="loading" @click="previewSell">预览最近优先等量配对</el-button>
    <p class="muted">优先最近买入批次，跨批次尽量等量扣减；容量不足或整数余股由服务端分配，差价计入个股已实现收益。</p>
    <el-divider />
    <div v-if="preview" class="preview">
      <div :class="['preview-summary', { 'm-preview-summary': isMobile }]">
        <div><span class="preview-label">预计净回款</span><strong class="num">{{ formatMoney(preview.cashIn, previewCurrency) }}</strong></div>
        <div><span class="preview-label">已实现收益</span><strong :class="['num', trendClass(preview.realizedPnl)]">{{ formatMoney(preview.realizedPnl, previewCurrency) }}</strong></div>
        <div><span class="preview-label">预览成交量</span><strong class="num">{{ formatQuantity(preview.qty) }}</strong></div>
      </div>
      <el-table v-if="!isMobile" :data="preview.matches" row-key="lotId" size="small" class="match-table">
        <el-table-column label="买入时间" min-width="170">
          <template #default="{ row }"><strong class="match-primary">{{ formatDateTime(row.buyExecutedAt) }}</strong></template>
        </el-table-column>
        <el-table-column label="原始成交价" min-width="135">
          <template #default="{ row }"><strong class="match-primary">{{ formatMoney(row.buyPrice, previewCurrency) }}</strong><div class="match-secondary">{{ previewCurrency ?? '币种未知' }}</div></template>
        </el-table-column>
        <el-table-column label="本次配对数量" min-width="230">
          <template #default="{ row }">
            <strong class="match-primary">{{ formatQuantity(row.matchedQty) }}</strong>
            <div class="match-secondary">原始买入 {{ formatQuantity(row.buyQty) }} · 预览前可用 {{ formatQuantity(row.availableQty) }}</div>
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
              <span class="preview-label">原始价</span>
              <strong class="match-primary num">{{ formatMoney(row.buyPrice, previewCurrency) }}</strong>
            </div>
            <div class="m-match-cell">
              <span class="preview-label">本次配对</span>
              <strong class="match-primary num">{{ formatQuantity(row.matchedQty) }} 股</strong>
              <span class="match-secondary num">买 {{ formatQuantity(row.buyQty) }} / 用 {{ formatQuantity(row.availableQty) }}</span>
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
.sell-form { max-width: 520px; }
.sell-form :deep(.el-date-editor) { width: 100%; }
.preview-summary { display: flex; flex-wrap: wrap; gap: 20px 36px; padding: 14px 0; border-top: 1px solid var(--el-border-color-lighter); border-bottom: 1px solid var(--el-border-color-lighter); }
.preview-summary > div { display: flex; min-width: 140px; flex-direction: column; gap: 4px; }
.preview-label, .match-secondary { color: var(--el-text-color-secondary); font-size: 12px; }
.match-primary { color: var(--el-text-color-primary); }
.match-table { margin-top: 16px; }
.lot-code { border-bottom: 1px dotted currentColor; cursor: help; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.preview-placeholder { padding: 28px 0; text-align: center; }
@media (max-width: 600px) {
  .preview-summary { gap: 12px 20px; }
  .preview-summary > div { min-width: 120px; }
}
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
