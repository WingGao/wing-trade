<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { Currency, StrategyRow, StrategyRowInput, StrategyRowStatus, StrategyRowUpdate } from '../api/types'
import { quantityUnit } from '../utils/format'
import { useViewport } from '../utils/viewport'

const props = withDefaults(defineProps<{
  modelValue: boolean
  currency?: Currency | null
  isFund?: boolean
  editingRow?: StrategyRow | null
  submitting?: boolean
}>(), {
  currency: 'CNY',
  isFund: false,
  editingRow: null,
  submitting: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [payload: StrategyRowInput | StrategyRowUpdate, rowId?: string]
}>()

const { isMobile } = useViewport()
const formError = ref('')

const form = reactive({
  buyPrice: '',
  buyQty: '',
  sellPrice: '',
  sellQty: '',
  status: 'PENDING' as StrategyRowStatus,
  note: '',
})

const isEdit = computed(() => Boolean(props.editingRow))
const unitLabel = computed(() => quantityUnit(props.isFund))
const currencySymbol = computed(() => (props.currency === 'USD' ? '$' : '¥'))

/** 去除字符串中不必要的尾部小数零，提升编辑态可读性。 */
function formatEditableNumber(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return ''
  const trimmed = value.trim()
  if (!trimmed.includes('.')) return trimmed
  return trimmed.replace(/\.?0+$/, '')
}

/** 弹窗打开时按编辑行或默认空表单重置输入值。 */
function resetForm(): void {
  formError.value = ''
  if (props.editingRow) {
    form.buyPrice = formatEditableNumber(props.editingRow.buyPrice)
    form.buyQty = formatEditableNumber(props.editingRow.buyQty)
    form.sellPrice = formatEditableNumber(props.editingRow.sellPrice)
    form.sellQty = formatEditableNumber(props.editingRow.sellQty)
    form.status = props.editingRow.status
    form.note = props.editingRow.note ?? ''
  } else {
    form.buyPrice = ''
    form.buyQty = ''
    form.sellPrice = ''
    form.sellQty = ''
    form.status = 'PENDING'
    form.note = ''
  }
}

watch(
  () => [props.modelValue, props.editingRow] as const,
  ([visible]) => {
    if (visible) resetForm()
  },
  { immediate: true },
)

/** 校验可选数字输入框：空串返回 null，非法或非正数返回错误说明。 */
function validateOptionalPositive(raw: string, label: string): { value: string | null; error?: string } {
  const cleaned = raw.trim().replace(/,/g, '')
  if (!cleaned) return { value: null }
  if (!/^[+-]?\d+(\.\d+)?$/.test(cleaned)) {
    return { value: null, error: `${label}请输入有效数字` }
  }
  const parsed = Number(cleaned)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return { value: null, error: `${label}必须大于 0` }
  }
  return { value: cleaned }
}

/** 校验表单并向父组件提交新增或局部更新载荷。 */
function handleSubmit(): void {
  formError.value = ''
  const buyPrice = validateOptionalPositive(form.buyPrice, '买入价')
  const buyQty = validateOptionalPositive(form.buyQty, '买入数量')
  const sellPrice = validateOptionalPositive(form.sellPrice, '卖出价')
  const sellQty = validateOptionalPositive(form.sellQty, '卖出数量')

  const firstError = buyPrice.error || buyQty.error || sellPrice.error || sellQty.error
  if (firstError) {
    formError.value = firstError
    return
  }

  if (
    buyPrice.value === null &&
    buyQty.value === null &&
    sellPrice.value === null &&
    sellQty.value === null
  ) {
    formError.value = '请至少填写一项买入或卖出的价格/数量'
    return
  }

  const trimmedNote = form.note.trim()
  if (trimmedNote.length > 256) {
    ElMessage.warning('备注长度不能超过 256 个字符')
    return
  }

  const payload: StrategyRowInput = {
    buyPrice: buyPrice.value,
    buyQty: buyQty.value,
    sellPrice: sellPrice.value,
    sellQty: sellQty.value,
    status: form.status,
    note: trimmedNote || null,
  }
  emit('submit', payload, props.editingRow?.id)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="isEdit ? '编辑策略档位' : '新增策略档位'"
    :width="isMobile ? '94vw' : '500px'"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert
      v-if="formError"
      :title="formError"
      type="error"
      :closable="false"
      class="dialog-alert"
    />
    <el-form :label-position="isMobile ? 'top' : 'right'" label-width="88px" class="strategy-row-form">
      <div class="row-pair-grid">
        <el-form-item :label="`买入价 (${currencySymbol})`">
          <el-input v-model="form.buyPrice" placeholder="留空表示不设买价" inputmode="decimal" clearable />
        </el-form-item>
        <el-form-item :label="`买入数量 (${unitLabel})`">
          <el-input v-model="form.buyQty" placeholder="留空表示不设买量" inputmode="decimal" clearable />
        </el-form-item>
      </div>
      <div class="row-pair-grid">
        <el-form-item :label="`卖出价 (${currencySymbol})`">
          <el-input v-model="form.sellPrice" placeholder="留空表示不设卖价" inputmode="decimal" clearable />
        </el-form-item>
        <el-form-item :label="`卖出数量 (${unitLabel})`">
          <el-input v-model="form.sellQty" placeholder="留空表示不设卖量" inputmode="decimal" clearable />
        </el-form-item>
      </div>
      <el-form-item label="执行状态">
        <el-radio-group v-model="form.status" size="small">
          <el-radio-button value="PENDING">待执行</el-radio-button>
          <el-radio-button value="BOUGHT">已买入</el-radio-button>
          <el-radio-button value="COMPLETED">已完成</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="备注">
        <el-input
          v-model="form.note"
          type="textarea"
          :rows="2"
          maxlength="256"
          show-word-limit
          placeholder="例如：第一档支撑位 / 回踩均线低吸"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="handleSubmit">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.dialog-alert { margin-bottom: 12px; }
.row-pair-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 12px;
}
@media (max-width: 768px) {
  .row-pair-grid {
    grid-template-columns: 1fr;
  }
}
</style>
