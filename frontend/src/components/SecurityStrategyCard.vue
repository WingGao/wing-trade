<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type {
  Currency,
  Quote,
  StrategyImportMode,
  StrategyRow,
  StrategyRowInput,
  StrategyRowStatus,
  StrategyRowUpdate,
} from '../api/types'
import { formatMoney, formatQuantity, priceDigits, quantityUnit } from '../utils/format'
import { copyTextToClipboard, STRATEGY_TABLE_AI_PROMPT } from '../utils/strategyMarkdown'
import { useViewport } from '../utils/viewport'
import StrategyMarkdownImportDialog from './StrategyMarkdownImportDialog.vue'
import StrategyRowDialog from './StrategyRowDialog.vue'

const props = withDefaults(defineProps<{
  securityId: string
  currency?: Currency | null
  isFund?: boolean
  quote?: Quote | null
  embedded?: boolean
}>(), {
  currency: 'CNY',
  isFund: false,
  quote: null,
  embedded: false,
})

const { isMobile } = useViewport()

const rows = ref<StrategyRow[]>([])
const loading = ref(false)
const loadError = ref('')
const submitting = ref(false)
const statusUpdatingId = ref<string | null>(null)
const importDialogVisible = ref(false)
const rowDialogVisible = ref(false)
const promptDialogVisible = ref(false)
const editingRow = ref<StrategyRow | null>(null)

let requestGeneration = 0

const digits = computed(() => priceDigits(props.isFund, 3))
const unitLabel = computed(() => quantityUnit(props.isFund))
const qtyTarget = computed(() => ({ currency: props.currency, securityType: props.isFund ? 'FUND' : 'STOCK' }))

const currentPriceNum = computed<number | null>(() => {
  const raw = props.quote?.lastPrice
  if (raw === null || raw === undefined || raw === '') return null
  const num = Number(raw)
  return Number.isFinite(num) && num > 0 ? num : null
})

const STATUS_OPTIONS: Array<{ value: StrategyRowStatus; label: string; tagType: 'info' | 'warning' | 'success' }> = [
  { value: 'PENDING', label: '待执行', tagType: 'info' },
  { value: 'BOUGHT', label: '已买入', tagType: 'warning' },
  { value: 'COMPLETED', label: '已完成', tagType: 'success' },
]

/** 判断当前实时现价是否已触达该策略档位的买入价（仅待执行状态高亮）。 */
function isBuyHit(row: StrategyRow): boolean {
  if (row.status !== 'PENDING' || currentPriceNum.value === null || !row.buyPrice) return false
  const target = Number(row.buyPrice)
  return Number.isFinite(target) && target > 0 && currentPriceNum.value <= target
}

/** 判断当前实时现价是否已触达该策略档位的卖出价（待执行或已买入状态高亮）。 */
function isSellHit(row: StrategyRow): boolean {
  if (row.status === 'COMPLETED' || currentPriceNum.value === null || !row.sellPrice) return false
  const target = Number(row.sellPrice)
  return Number.isFinite(target) && target > 0 && currentPriceNum.value >= target
}

/** 加载当前标的的买卖策略表（按用户+标的维度共享，并用代次防止切换串台）。 */
async function loadRows(): Promise<void> {
  if (!props.securityId) {
    rows.value = []
    return
  }
  const currentGen = ++requestGeneration
  loading.value = true
  loadError.value = ''
  try {
    const data = await api.strategyRows(props.securityId)
    if (currentGen !== requestGeneration) return
    rows.value = data
  } catch (error) {
    if (currentGen !== requestGeneration) return
    loadError.value = error instanceof Error ? error.message : '加载策略表失败'
  } finally {
    if (currentGen === requestGeneration) loading.value = false
  }
}

watch(
  () => props.securityId,
  () => {
    void loadRows()
  },
  { immediate: true },
)

/** 一键复制固定静态 AI 提示词，失败时自动打开提示词预览弹窗供手动复制。 */
async function handleCopyPrompt(): Promise<void> {
  const ok = await copyTextToClipboard(STRATEGY_TABLE_AI_PROMPT)
  if (ok) {
    ElMessage.success('已复制固定 AI 提示词')
  } else {
    promptDialogVisible.value = true
    ElMessage.warning('自动复制受限，请在弹窗中手动复制提示词')
  }
}

/** 打开新增策略档位弹窗。 */
function openCreateDialog(): void {
  editingRow.value = null
  rowDialogVisible.value = true
}

/** 打开编辑单条策略档位弹窗。 */
function openEditDialog(row: StrategyRow): void {
  editingRow.value = row
  rowDialogVisible.value = true
}

/** 提交 Markdown 批量导入请求并刷新本地策略表。 */
async function handleImport(payload: { mode: StrategyImportMode; rows: StrategyRowInput[] }): Promise<void> {
  submitting.value = true
  try {
    rows.value = await api.importStrategyRows(props.securityId, payload)
    importDialogVisible.value = false
    ElMessage.success(payload.mode === 'replace' ? `已覆盖导入 ${payload.rows.length} 档策略` : `已追加导入 ${payload.rows.length} 档策略`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '导入策略表失败')
  } finally {
    submitting.value = false
  }
}

/** 提交单行新增或编辑请求并同步更新本地列表。 */
async function handleRowSubmit(payload: StrategyRowInput | StrategyRowUpdate, rowId?: string): Promise<void> {
  submitting.value = true
  try {
    if (rowId) {
      const updated = await api.updateStrategyRow(props.securityId, rowId, payload as StrategyRowUpdate)
      rows.value = rows.value.map((item) => (item.id === updated.id ? updated : item))
      ElMessage.success('策略档位已更新')
    } else {
      const created = await api.createStrategyRow(props.securityId, payload as StrategyRowInput)
      rows.value = [...rows.value, created]
      ElMessage.success('已新增策略档位')
    }
    rowDialogVisible.value = false
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存策略档位失败')
  } finally {
    submitting.value = false
  }
}

/** 在策略表行内快捷切换档位执行状态并立即落库。 */
async function handleStatusChange(row: StrategyRow, nextStatus: StrategyRowStatus): Promise<void> {
  if (row.status === nextStatus || statusUpdatingId.value === row.id) return
  statusUpdatingId.value = row.id
  try {
    const updated = await api.updateStrategyRow(props.securityId, row.id, { status: nextStatus })
    rows.value = rows.value.map((item) => (item.id === updated.id ? updated : item))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '更新状态失败')
  } finally {
    statusUpdatingId.value = null
  }
}

/** 删除单条策略档位。 */
async function handleDeleteRow(row: StrategyRow): Promise<void> {
  try {
    await ElMessageBox.confirm('确认删除该策略档位吗？', '删除策略档位', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await api.deleteStrategyRow(props.securityId, row.id)
    rows.value = rows.value.filter((item) => item.id !== row.id)
    ElMessage.success('已删除策略档位')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '删除失败')
  }
}

/** 二次确认后清空当前标的的全部策略档位。 */
async function handleClearAll(): Promise<void> {
  if (rows.value.length === 0) return
  try {
    await ElMessageBox.confirm(`确认清空当前标的的全部 ${rows.value.length} 条策略档位吗？此操作不可恢复。`, '清空策略表', {
      type: 'warning',
      confirmButtonText: '确认清空',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await api.clearStrategyRows(props.securityId)
    rows.value = []
    ElMessage.success('已清空策略表')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '清空失败')
  }
}
</script>

<template>
  <el-card v-if="!isMobile" class="panel strategy-card">
    <template #header>
      <div class="panel-title strategy-heading">
        <div class="strategy-title-group">
          <span>买卖策略表</span>
          <span class="muted">共 {{ rows.length }} 档 · 跨账户共享</span>
        </div>
        <div class="toolbar strategy-toolbar">
          <el-button-group :size="embedded ? 'small' : 'default'">
            <el-button @click="handleCopyPrompt">复制 AI 提示词</el-button>
            <el-button @click="promptDialogVisible = true">查看</el-button>
          </el-button-group>
          <el-button :size="embedded ? 'small' : 'default'" type="primary" plain @click="importDialogVisible = true">
            导入 Markdown/CSV
          </el-button>
          <el-button :size="embedded ? 'small' : 'default'" @click="openCreateDialog">新增档位</el-button>
          <el-button
            v-if="rows.length > 0"
            :size="embedded ? 'small' : 'default'"
            type="danger"
            plain
            @click="handleClearAll"
          >
            清空
          </el-button>
        </div>
      </div>
    </template>

    <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" class="status-alert" />

    <div v-loading="loading">
      <div v-if="!loading && rows.length === 0" class="strategy-empty">
        <p class="muted">暂无买卖策略档位。可复制固定 AI 提示词生成表格后直接粘贴导入，或手动新增档位。</p>
        <div class="strategy-empty-actions">
          <el-button size="small" type="primary" @click="importDialogVisible = true">导入 Markdown / CSV</el-button>
          <el-button size="small" @click="handleCopyPrompt">复制 AI 提示词</el-button>
          <el-button size="small" plain @click="openCreateDialog">手动新增档位</el-button>
        </div>
      </div>

      <el-table v-else :data="rows" size="small" stripe class="strategy-table">
        <el-table-column type="index" label="#" width="42" />
        <el-table-column label="买入价" min-width="112">
          <template #default="{ row }">
            <div class="price-cell num">
              <span :class="{ 'hit-buy': isBuyHit(row) }">
                {{ row.buyPrice ? formatMoney(row.buyPrice, currency, digits) : '—' }}
              </span>
              <el-tag v-if="isBuyHit(row)" size="small" type="success" effect="dark" class="hit-tag">到买价</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="`买入数量(${unitLabel})`" min-width="96">
          <template #default="{ row }">
            <span class="num">{{ row.buyQty ? formatQuantity(row.buyQty, qtyTarget) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="卖出价" min-width="112">
          <template #default="{ row }">
            <div class="price-cell num">
              <span :class="{ 'hit-sell': isSellHit(row) }">
                {{ row.sellPrice ? formatMoney(row.sellPrice, currency, digits) : '—' }}
              </span>
              <el-tag v-if="isSellHit(row)" size="small" type="danger" effect="dark" class="hit-tag">到卖价</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="`卖出数量(${unitLabel})`" min-width="96">
          <template #default="{ row }">
            <span class="num">{{ row.sellQty ? formatQuantity(row.sellQty, qtyTarget) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="114">
          <template #default="{ row }">
            <el-select
              :model-value="row.status"
              size="small"
              :loading="statusUpdatingId === row.id"
              aria-label="执行状态"
              style="width: 92px"
              @change="(val: StrategyRowStatus) => handleStatusChange(row, val)"
            >
              <el-option v-for="opt in STATUS_OPTIONS" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ 'is-completed-text': row.status === 'COMPLETED' }">{{ row.note || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="104" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEditDialog(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDeleteRow(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-card>

  <section v-else class="m-section m-strategy-section">
    <div class="m-section-head m-strategy-head">
      <span>买卖策略表 <span class="m-section-sub">{{ rows.length }} 档</span></span>
      <div class="m-strategy-tools">
        <el-button size="small" plain @click="handleCopyPrompt">复制提示词</el-button>
        <el-button size="small" type="primary" plain @click="importDialogVisible = true">导入</el-button>
        <el-button size="small" @click="openCreateDialog">新增</el-button>
      </div>
    </div>

    <div v-if="loadError" class="m-compact-note is-warning">{{ loadError }}</div>

    <div v-loading="loading" class="m-strategy-body">
      <div v-if="!loading && rows.length === 0" class="m-strategy-empty">
        <p class="m-section-sub">暂无策略档位，可复制固定 AI 提示词生成 Markdown 表格后粘贴导入。</p>
        <div class="m-strategy-empty-btns">
          <el-button size="small" link type="primary" @click="promptDialogVisible = true">查看提示词</el-button>
          <el-button size="small" link type="primary" @click="importDialogVisible = true">去导入</el-button>
        </div>
      </div>

      <div v-else class="m-strategy-list">
        <article
          v-for="(row, idx) in rows"
          :key="row.id"
          :class="['m-strategy-item', { 'is-completed': row.status === 'COMPLETED' }]"
        >
          <div class="m-strategy-row-top">
            <div class="m-strategy-row-left">
              <span class="m-strategy-idx">#{{ idx + 1 }}</span>
              <el-tag v-if="isBuyHit(row)" size="small" type="success" effect="dark">到买价</el-tag>
              <el-tag v-if="isSellHit(row)" size="small" type="danger" effect="dark">到卖价</el-tag>
            </div>
            <div class="m-strategy-row-right">
              <el-select
                :model-value="row.status"
                size="small"
                :loading="statusUpdatingId === row.id"
                aria-label="执行状态"
                class="m-status-select"
                @change="(val: StrategyRowStatus) => handleStatusChange(row, val)"
              >
                <el-option v-for="opt in STATUS_OPTIONS" :key="opt.value" :label="opt.label" :value="opt.value" />
              </el-select>
              <el-button link type="primary" size="small" @click="openEditDialog(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="handleDeleteRow(row)">删</el-button>
            </div>
          </div>

          <div class="m-strategy-grid num">
            <div class="m-strategy-cell">
              <span class="m-strategy-label">买入价 / 量</span>
              <strong :class="['m-strategy-val', { 'hit-buy': isBuyHit(row) }]">
                {{ row.buyPrice ? formatMoney(row.buyPrice, currency, digits) : '—' }}
              </strong>
              <span class="m-strategy-sub">
                {{ row.buyQty ? `${formatQuantity(row.buyQty, qtyTarget)} ${unitLabel}` : '—' }}
              </span>
            </div>
            <div class="m-strategy-cell">
              <span class="m-strategy-label">卖出价 / 量</span>
              <strong :class="['m-strategy-val', { 'hit-sell': isSellHit(row) }]">
                {{ row.sellPrice ? formatMoney(row.sellPrice, currency, digits) : '—' }}
              </strong>
              <span class="m-strategy-sub">
                {{ row.sellQty ? `${formatQuantity(row.sellQty, qtyTarget)} ${unitLabel}` : '—' }}
              </span>
            </div>
          </div>

          <p v-if="row.note" class="m-strategy-note">{{ row.note }}</p>
        </article>

        <div class="m-strategy-footer">
          <el-button size="small" link @click="promptDialogVisible = true">查看固定 AI 提示词</el-button>
          <el-button size="small" link type="danger" @click="handleClearAll">清空全部</el-button>
        </div>
      </div>
    </div>
  </section>

  <StrategyMarkdownImportDialog
    v-model="importDialogVisible"
    :currency="currency"
    :is-fund="isFund"
    :has-existing-rows="rows.length > 0"
    :submitting="submitting"
    @import="handleImport"
  />

  <StrategyRowDialog
    v-model="rowDialogVisible"
    :currency="currency"
    :is-fund="isFund"
    :editing-row="editingRow"
    :submitting="submitting"
    @submit="handleRowSubmit"
  />

  <el-dialog
    v-model="promptDialogVisible"
    title="固定 AI 策略表提示词"
    :width="isMobile ? '94vw' : '620px'"
  >
    <p class="prompt-dialog-desc">将下方固定提示词复制发送给任意 AI，即可生成可直接导入本页的标准 Markdown 策略表：</p>
    <div class="prompt-dialog-box">
      <pre class="prompt-dialog-pre">{{ STRATEGY_TABLE_AI_PROMPT }}</pre>
    </div>
    <template #footer>
      <el-button @click="promptDialogVisible = false">关闭</el-button>
      <el-button type="primary" @click="handleCopyPrompt">一键复制提示词</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.strategy-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.strategy-title-group { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.strategy-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.strategy-empty {
  padding: 20px 12px;
  text-align: center;
}
.strategy-empty p { margin: 0 0 12px; font-size: 13px; }
.strategy-empty-actions { display: flex; justify-content: center; gap: 8px; flex-wrap: wrap; }
.price-cell { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.hit-buy { color: #16a34a; font-weight: 700; }
.hit-sell { color: #dc2626; font-weight: 700; }
.hit-tag { height: 18px; line-height: 16px; padding: 0 4px; font-size: 11px; }
.is-completed-text { color: #94a3b8; text-decoration: line-through; }

.m-strategy-head { flex-wrap: wrap; gap: 6px; }
.m-strategy-tools { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.m-strategy-body { padding: 6px 8px 8px; }
.m-strategy-empty { padding: 10px 4px; text-align: center; }
.m-strategy-empty p { margin: 0 0 6px; }
.m-strategy-empty-btns { display: flex; justify-content: center; gap: 12px; }
.m-strategy-list { display: flex; flex-direction: column; gap: 8px; }
.m-strategy-item {
  padding: 8px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #ffffff;
}
.m-strategy-item.is-completed { opacity: 0.72; background: #f8fafc; }
.m-strategy-row-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 6px;
}
.m-strategy-row-left { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; min-width: 0; }
.m-strategy-row-right { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.m-strategy-idx { font-size: 11px; font-weight: 700; color: #64748b; }
.m-status-select { width: 82px; }
.m-strategy-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.m-strategy-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.m-strategy-label { font-size: 11px; color: #64748b; }
.m-strategy-val { font-size: 14px; font-weight: 600; color: #0f172a; overflow-wrap: anywhere; }
.m-strategy-sub { font-size: 12px; color: #475569; }
.m-strategy-note {
  margin: 6px 0 0;
  padding-top: 6px;
  border-top: 1px dashed #e2e8f0;
  font-size: 12px;
  color: #475569;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.m-strategy-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 2px;
}

.prompt-dialog-desc { margin: 0 0 10px; font-size: 13px; color: var(--el-text-color-secondary); }
.prompt-dialog-box {
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  border-radius: 6px;
  background: #0f172a;
  color: #e2e8f0;
}
.prompt-dialog-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.55;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
</style>
