<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { Currency, StrategyImportMode, StrategyRowInput } from '../api/types'
import { formatMoney, formatQuantity, priceDigits, quantityUnit } from '../utils/format'
import {
  copyTextToClipboard,
  parseStrategyMarkdown,
  STRATEGY_TABLE_AI_PROMPT,
} from '../utils/strategyMarkdown'
import { useViewport } from '../utils/viewport'

const props = withDefaults(defineProps<{
  modelValue: boolean
  currency?: Currency | null
  isFund?: boolean
  hasExistingRows?: boolean
  submitting?: boolean
}>(), {
  currency: 'CNY',
  isFund: false,
  hasExistingRows: false,
  submitting: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  import: [payload: { mode: StrategyImportMode; rows: StrategyRowInput[] }]
}>()

const { isMobile } = useViewport()
const rawMarkdown = ref('')
const importMode = ref<StrategyImportMode>('replace')
const showPromptPreview = ref(false)

const parsed = computed(() => parseStrategyMarkdown(rawMarkdown.value))
const canSubmit = computed(() => parsed.value.rows.length > 0 && parsed.value.errors.length === 0)
const qtyTarget = computed(() => ({ currency: props.currency, securityType: props.isFund ? 'FUND' : 'STOCK' }))
const digits = computed(() => priceDigits(props.isFund, 3))
const unitLabel = computed(() => quantityUnit(props.isFund))

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      rawMarkdown.value = ''
      importMode.value = 'replace'
      showPromptPreview.value = false
    }
  },
)

/** 一键复制固定静态 AI 提示词，便于直接发给外部 AI 生成标准策略表。 */
async function handleCopyPrompt(): Promise<void> {
  const ok = await copyTextToClipboard(STRATEGY_TABLE_AI_PROMPT)
  if (ok) {
    ElMessage.success('已复制固定 AI 提示词，可直接粘贴给 AI 生成策略表')
  } else {
    showPromptPreview.value = true
    ElMessage.warning('自动复制受限，已展开提示词全文，请手动全选复制')
  }
}

/** 确认导入已通过校验的策略档位列表。 */
function handleConfirmImport(): void {
  if (!canSubmit.value) return
  emit('import', {
    mode: importMode.value,
    rows: parsed.value.rows,
  })
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="导入策略（支持 Markdown 表格 / CSV）"
    :width="isMobile ? '94vw' : '720px'"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="import-body">
      <div class="prompt-helper-bar">
        <span class="prompt-helper-tip">还没有标准表格？复制固定提示词发给 AI 即可生成</span>
        <div class="prompt-helper-actions">
          <el-button size="small" type="primary" plain @click="handleCopyPrompt">复制 AI 提示词</el-button>
          <el-button size="small" link @click="showPromptPreview = !showPromptPreview">
            {{ showPromptPreview ? '收起提示词' : '查看提示词' }}
          </el-button>
        </div>
      </div>

      <div v-if="showPromptPreview" class="prompt-preview-box">
        <pre class="prompt-pre">{{ STRATEGY_TABLE_AI_PROMPT }}</pre>
      </div>

      <el-input
        v-model="rawMarkdown"
        type="textarea"
        :rows="isMobile ? 6 : 7"
        placeholder="直接粘贴 Markdown 表格或 CSV 文本（支持逗号分隔与代码块包裹）：&#10;| 买入价 | 买入数量 | 卖出价 | 卖出数量 | 备注 |&#10;| --- | --- | --- | --- | --- |&#10;| 10.50 | 1000 | 11.80 | 1000 | 支撑位低吸 |&#10;&#10;或 CSV：&#10;买入价,买入数量,卖出价,卖出数量,备注&#10;10.50,1000,11.80,1000,支撑位低吸"
      />

      <div class="mode-row">
        <span class="mode-label">导入方式</span>
        <el-radio-group v-model="importMode" size="small">
          <el-radio-button value="replace">覆盖当前策略表</el-radio-button>
          <el-radio-button value="append">追加到末尾</el-radio-button>
        </el-radio-group>
        <span v-if="hasExistingRows && importMode === 'replace'" class="mode-warn">
          将替换现有全部档位
        </span>
      </div>

      <el-alert
        v-for="(warning, idx) in parsed.warnings"
        :key="`warn-${idx}`"
        :title="warning"
        type="warning"
        :closable="false"
        class="parse-alert"
      />

      <el-alert
        v-if="parsed.errors.length > 0"
        type="error"
        :closable="false"
        class="parse-alert"
        :title="`解析发现 ${parsed.errors.length} 处错误，请修正后再导入`"
      >
        <ul class="error-list">
          <li v-for="(err, idx) in parsed.errors" :key="`err-${idx}`">
            第 {{ err.line }} 行：{{ err.message }}
          </li>
        </ul>
      </el-alert>

      <div v-if="parsed.rows.length > 0" class="preview-section">
        <div class="preview-head">
          <span>解析预览（共 {{ parsed.rows.length }} 档）</span>
        </div>
        <el-table v-if="!isMobile" :data="parsed.rows" size="small" max-height="240" border>
          <el-table-column type="index" label="#" width="42" />
          <el-table-column label="买入价" min-width="96">
            <template #default="{ row }">
              <span class="num">{{ row.buyPrice ? formatMoney(row.buyPrice, currency, digits) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="`买入数量(${unitLabel})`" min-width="96">
            <template #default="{ row }">
              <span class="num">{{ row.buyQty ? formatQuantity(row.buyQty, qtyTarget) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="卖出价" min-width="96">
            <template #default="{ row }">
              <span class="num">{{ row.sellPrice ? formatMoney(row.sellPrice, currency, digits) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="`卖出数量(${unitLabel})`" min-width="96">
            <template #default="{ row }">
              <span class="num">{{ row.sellQty ? formatQuantity(row.sellQty, qtyTarget) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="备注" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">{{ row.note || '—' }}</template>
          </el-table-column>
        </el-table>

        <div v-else class="m-preview-list">
          <div v-for="(row, idx) in parsed.rows" :key="idx" class="m-preview-item">
            <div class="m-preview-top">
              <span class="m-preview-idx">#{{ idx + 1 }}</span>
              <span v-if="row.note" class="m-preview-note">{{ row.note }}</span>
            </div>
            <div class="m-preview-grid num">
              <div>
                <span class="m-preview-label">买：</span>
                <span>{{ row.buyPrice ? formatMoney(row.buyPrice, currency, digits) : '—' }}</span>
                <span v-if="row.buyQty"> × {{ formatQuantity(row.buyQty, qtyTarget) }}</span>
              </div>
              <div>
                <span class="m-preview-label">卖：</span>
                <span>{{ row.sellPrice ? formatMoney(row.sellPrice, currency, digits) : '—' }}</span>
                <span v-if="row.sellQty"> × {{ formatQuantity(row.sellQty, qtyTarget) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :disabled="!canSubmit" :loading="submitting" @click="handleConfirmImport">
        确认导入{{ parsed.rows.length ? ` (${parsed.rows.length} 档)` : '' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.import-body { display: flex; flex-direction: column; gap: 12px; }
.prompt-helper-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 10px;
  border-radius: 6px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}
.prompt-helper-tip { font-size: 12px; color: #475569; }
.prompt-helper-actions { display: flex; align-items: center; gap: 6px; }
.prompt-preview-box {
  max-height: 200px;
  overflow: auto;
  padding: 10px 12px;
  border-radius: 6px;
  background: #0f172a;
  color: #e2e8f0;
}
.prompt-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.5;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.mode-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.mode-label { font-size: 13px; color: var(--el-text-color-secondary); }
.mode-warn { font-size: 12px; color: #d97706; }
.parse-alert { margin: 0; }
.error-list { margin: 4px 0 0; padding-left: 18px; font-size: 12px; }
.preview-head { font-size: 13px; font-weight: 600; margin-bottom: 6px; color: var(--el-text-color-primary); }
.m-preview-list { display: flex; flex-direction: column; gap: 6px; max-height: 220px; overflow-y: auto; }
.m-preview-item {
  padding: 6px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
}
.m-preview-top { display: flex; align-items: baseline; gap: 6px; margin-bottom: 4px; }
.m-preview-idx { font-size: 11px; font-weight: 600; color: #64748b; flex-shrink: 0; }
.m-preview-note { font-size: 12px; color: #334155; overflow-wrap: anywhere; }
.m-preview-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; font-size: 12px; }
.m-preview-label { color: #64748b; }
</style>
