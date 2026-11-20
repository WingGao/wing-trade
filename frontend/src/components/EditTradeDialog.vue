<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Trade, TradeUpdate, TradeUpdateRead } from '../api/types'
import { useLedgerStore } from '../stores/ledger'
import { useViewport } from '../utils/viewport'

const props = defineProps<{ trade: Trade | null; securityLabel: string }>()
const emit = defineEmits<{ saved: [result: TradeUpdateRead] }>()
const visible = defineModel<boolean>({ default: false })
const ledger = useLedgerStore()
const { isMobile } = useViewport()
const saving = ref(false)
const error = ref('')
const form = reactive({ executedAt: '', qty: '', price: '', commission: '', stampDuty: '', transferFee: '', otherFee: '', memo: '', reason: '' })
let alive = true

/** 既有 SQLite 响应的无时区时间按 UTC 标注，只追加时区，不重建或截断微秒。 */
function formTimestamp(value: string): string {
  return /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`
}

/** 完整预填服务端原始字符串，不使用整数数量或三位价格展示格式。 */
function fillForm() {
  const trade = props.trade
  if (!trade) return
  Object.assign(form, { executedAt: formTimestamp(trade.executedAt), qty: trade.qty, price: trade.price, commission: trade.commission, stampDuty: trade.stampDuty, transferFee: trade.transferFee, otherFee: trade.otherFee, memo: trade.memo ?? '', reason: '' })
  error.value = ''
}

/** 检查普通十进制文本的符号与正值约束，提交时仍保持字符串精度。 */
function validDecimal(value: string, positive: boolean): boolean {
  const decimal = value.trim()
  return /^\+?(?:\d+(?:\.\d*)?|\.\d+)$/.test(decimal) && (!positive || /[1-9]/.test(decimal))
}

/** 仅提交实际变更字段和打开时的版本，保存失败保留全部输入。 */
async function submit() {
  const trade = props.trade
  if (!trade || saving.value) return
  error.value = ''
  if (!validDecimal(form.qty, true) || !validDecimal(form.price, true)) { error.value = '成交数量和价格必须为正数'; return }
  const feeKeys = ['commission', 'stampDuty', 'transferFee', 'otherFee'] as const
  if (feeKeys.some((key) => !validDecimal(form[key], false))) { error.value = '各项费用必须为非负数'; return }
  if (!form.executedAt.trim() || Number.isNaN(Date.parse(form.executedAt))) { error.value = '请输入带时区的有效成交时间'; return }
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(form.executedAt.trim())) { error.value = '成交时间须包含时区，例如 +08:00 或 Z'; return }
  const payload: TradeUpdate = { revision: trade.revision }
  const exactKeys = ['executedAt', 'qty', 'price', ...feeKeys] as const
  for (const key of exactKeys) {
    const original = key === 'executedAt' ? formTimestamp(trade.executedAt) : trade[key]
    if (form[key].trim() !== original) payload[key] = form[key].trim()
  }
  if (form.memo !== (trade.memo ?? '')) payload.memo = form.memo || null
  if (form.reason.trim()) payload.reason = form.reason.trim()
  saving.value = true
  try {
    const result = await api.updateTrade(trade.id, payload)
    ledger.invalidateDashboard()
    if (!alive || props.trade?.id !== trade.id) return
    visible.value = false
    ElMessage.success(result.trade.revision === trade.revision ? '交易没有实际修改' : '交易已保存，对应持仓需要刷新')
    emit('saved', result)
  } catch (cause) {
    if (alive) error.value = cause instanceof Error ? cause.message : '保存交易失败'
  } finally { saving.value = false }
}

watch(() => [visible.value, props.trade], () => { if (visible.value) fillForm() })
onBeforeUnmount(() => { alive = false })
</script>

<template>
  <el-dialog v-model="visible" title="编辑交易流水" :width="isMobile ? '94vw' : '560px'" top="5vh" class="edit-trade-dialog" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="status-alert" />
    <p class="muted">{{ securityLabel }} · {{ trade?.side === 'BUY' ? '买入' : '卖出' }}<br>账户 {{ trade?.accountId }} · 策略 {{ trade?.strategyId }}（归属和买卖方向不可修改）</p>
    <el-form :label-position="isMobile ? 'top' : 'right'" :label-width="isMobile ? undefined : '110px'" :disabled="saving" @submit.prevent="submit">
      <el-form-item label="成交时间"><el-input v-model="form.executedAt" aria-label="成交时间" placeholder="例如 2026-11-18T09:30:00+08:00" /><span class="muted">保留时区；+08:00 为北京时间，Z 为 UTC。</span></el-form-item>
      <el-form-item label="成交数量"><el-input v-model="form.qty" aria-label="成交数量" /></el-form-item>
      <el-form-item label="成交价格"><el-input v-model="form.price" aria-label="成交价格" /></el-form-item>
      <el-form-item label="佣金"><el-input v-model="form.commission" aria-label="佣金" /></el-form-item>
      <el-form-item label="印花税"><el-input v-model="form.stampDuty" aria-label="印花税" /></el-form-item>
      <el-form-item label="过户费"><el-input v-model="form.transferFee" aria-label="过户费" /></el-form-item>
      <el-form-item label="其他费用"><el-input v-model="form.otherFee" aria-label="其他费用" /></el-form-item>
      <el-form-item label="备注"><el-input v-model="form.memo" type="textarea" aria-label="备注" /></el-form-item>
      <el-form-item label="更正原因"><el-input v-model="form.reason" aria-label="更正原因" placeholder="选填，将记录在审计中" /></el-form-item>
    </el-form>
    <p class="muted">保存后持仓与收益需要手动刷新；已有显式止盈目标请在重新计算后核对。</p>
    <template #footer><el-button :disabled="saving" @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submit">保存修改</el-button></template>
  </el-dialog>
</template>

<style>
.edit-trade-dialog { max-height: 90vh; display: flex; flex-direction: column; }
.edit-trade-dialog .el-dialog__body { min-height: 0; overflow-y: auto; }
.edit-trade-dialog .el-dialog__header, .edit-trade-dialog .el-dialog__footer { flex-shrink: 0; }
</style>
