<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Security, SecurityCandidate } from '../api/types'
import { useViewport } from '../utils/viewport'

const props = defineProps<{
  modelValue: boolean
  security: Security
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: [security: Security]
}>()

const { isMobile } = useViewport()
const searchText = ref('')
const searching = ref(false)
const searchError = ref('')
const candidates = ref<SecurityCandidate[]>([])
const selectedProxySymbol = ref('')
const selectedProxyName = ref('')
const ratioPercent = ref('95')
const showAdvanced = ref(false)
const saving = ref(false)
let searchToken = 0
let debounceTimer: number | null = null

const hasBinding = computed(() => Boolean(props.security.proxySymbol))
const canSave = computed(() => Boolean(selectedProxySymbol.value.trim()) && !saving.value)

/** 将后端小数仓位系数（如 0.950000）转换为界面百分比文本（如 95）。 */
function toPercentText(ratio?: string | null): string {
  if (!ratio) return '95'
  const numeric = Number(ratio)
  if (!Number.isFinite(numeric) || numeric <= 0) return '95'
  const pct = numeric * 100
  return Number.isInteger(pct) ? String(pct) : pct.toFixed(2).replace(/\.?0+$/, '')
}

/** 弹窗打开时回填当前场外基金已绑定的参考标的与仓位系数。 */
function syncFromSecurity(): void {
  selectedProxySymbol.value = props.security.proxySymbol ?? ''
  selectedProxyName.value = props.security.proxyName ?? ''
  searchText.value = props.security.proxySymbol
    ? `${props.security.proxySymbol}${props.security.proxyName ? ` ${props.security.proxyName}` : ''}`
    : ''
  ratioPercent.value = toPercentText(props.security.trackingRatio)
  showAdvanced.value = ratioPercent.value !== '95'
  candidates.value = []
  searchError.value = ''
}

/** 根据输入的 6 位代码或名称搜索纯 A 股指数与场内 ETF 候选。 */
async function performSearch(keyword: string): Promise<void> {
  const trimmed = keyword.trim()
  if (!trimmed) {
    candidates.value = []
    searchError.value = ''
    return
  }
  const token = ++searchToken
  searching.value = true
  searchError.value = ''
  try {
    const result = await api.lookupSecurities(trimmed)
    if (token !== searchToken) return
    candidates.value = result.securities.filter((item) => ['SH', 'SZ', 'BJ'].includes(item.market.toUpperCase()))
    if (result.error) searchError.value = result.error
  } catch (cause) {
    if (token !== searchToken) return
    searchError.value = cause instanceof Error ? cause.message : '搜索参考标的失败'
  } finally {
    if (token === searchToken) searching.value = false
  }
}

/** 输入防抖搜索，并在输入完整规范代码（如 SH:000300）时允许直接识别。 */
function handleSearchInput(value: string): void {
  searchText.value = value
  const upper = value.trim().toUpperCase()
  const directMatch = upper.match(/^(SH|SZ|BJ):(\d{6})$/)
  if (directMatch) {
    selectedProxySymbol.value = `${directMatch[1]}:${directMatch[2]}`
    selectedProxyName.value = ''
  } else {
    selectedProxySymbol.value = ''
    selectedProxyName.value = ''
  }
  if (debounceTimer !== null) window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => {
    debounceTimer = null
    void performSearch(value)
  }, 220)
}

/** 选中下拉候选后回填规范代码与名称，无需手输其他字段。 */
function selectCandidate(candidate: SecurityCandidate): void {
  const symbol = `${candidate.market.toUpperCase()}:${candidate.symbol.toUpperCase()}`
  selectedProxySymbol.value = symbol
  selectedProxyName.value = candidate.name ?? ''
  searchText.value = `${symbol}${candidate.name ? ` ${candidate.name}` : ''}`
  candidates.value = []
}

/** 将界面百分比输入校验并换算为后端小数字符串（如 95 -> 0.950000）。 */
function resolveTrackingRatio(): string {
  const raw = ratioPercent.value.trim()
  const numeric = raw ? Number(raw) : 95
  if (!Number.isFinite(numeric) || numeric <= 0 || numeric > 150) {
    throw new Error('仓位系数必须在 0% 到 150% 之间')
  }
  return (numeric / 100).toFixed(6)
}

/** 保存场外基金跟踪参考标的配置并通知详情页立即刷新盘中估算。 */
async function save(): Promise<void> {
  if (!selectedProxySymbol.value.trim()) {
    ElMessage.warning('请先输入代码并在列表中选择一个 A 股指数或场内 ETF')
    return
  }
  saving.value = true
  try {
    const trackingRatio = resolveTrackingRatio()
    const updated = await api.updateSecurityProxy(props.security.id, {
      proxySymbol: selectedProxySymbol.value.trim().toUpperCase(),
      proxyName: selectedProxyName.value.trim() || null,
      trackingRatio,
    })
    ElMessage.success('已绑定跟踪参考标的，盘中将自动估算净值')
    emit('saved', updated)
    emit('update:modelValue', false)
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '保存跟踪标的失败')
  } finally {
    saving.value = false
  }
}

/** 清除当前场外基金的跟踪参考标的绑定，恢复纯官方净值展示。 */
async function clearBinding(): Promise<void> {
  saving.value = true
  try {
    const updated = await api.updateSecurityProxy(props.security.id, { proxySymbol: null })
    ElMessage.success('已解除跟踪标的绑定')
    emit('saved', updated)
    emit('update:modelValue', false)
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '解除绑定失败')
  } finally {
    saving.value = false
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) syncFromSecurity()
  },
)
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="设置盘中跟踪标的"
    :width="isMobile ? '94vw' : '500px'"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="proxy-desc">
      为 <strong>{{ security.name || security.symbol }}</strong> 绑定跟踪的 A 股宽基指数或场内 ETF（如
      <code>000300</code> 沪深300、<code>000905</code> 中证500、<code>399006</code> 创业板指、<code>510300</code>），盘中将按参考标的实时涨跌幅自动推算估算净值、盈亏与分时走势；晚间官方净值公布后自动切回官方净值。
    </p>

    <el-form label-position="top" @submit.prevent="save">
      <el-form-item label="搜索并选择参考标的（输入 6 位代码或名称）">
        <el-input
          :model-value="searchText"
          placeholder="例如：000300 / 沪深300 / 510300 / 399006"
          clearable
          @update:model-value="handleSearchInput"
        />
        <div v-if="searching" class="proxy-hint">正在搜索 A 股指数与场内 ETF…</div>
        <div v-if="searchError" class="proxy-error">{{ searchError }}</div>
        <ul v-if="candidates.length" class="proxy-candidates" role="listbox" aria-label="参考标的候选列表">
          <li v-for="item in candidates" :key="`${item.market}:${item.symbol}`">
            <button type="button" class="proxy-candidate-btn" @click="selectCandidate(item)">
              <span class="proxy-candidate-main">
                <strong>{{ item.name || item.symbol }}</strong>
                <span class="proxy-candidate-code">{{ item.market }}:{{ item.symbol }}</span>
              </span>
              <el-tag size="small" :type="item.securityType === 'INDEX' ? 'primary' : 'info'">
                {{ item.securityType === 'INDEX' ? '指数' : '场内' }}
              </el-tag>
            </button>
          </li>
        </ul>
      </el-form-item>

      <div v-if="selectedProxySymbol" class="proxy-selected">
        已选参考标的：<strong>{{ selectedProxyName || selectedProxySymbol }}</strong>
        <span class="proxy-selected-code">({{ selectedProxySymbol }})</span>
      </div>

      <div class="proxy-advanced-toggle">
        <el-button link type="primary" size="small" @click="showAdvanced = !showAdvanced">
          {{ showAdvanced ? '收起仓位系数 ▴' : `仓位系数（默认 ${ratioPercent}%，可选微调）▾` }}
        </el-button>
      </div>
      <el-form-item v-if="showAdvanced" label="估算仓位系数（%）">
        <el-input v-model="ratioPercent" placeholder="默认 95">
          <template #append>%</template>
        </el-input>
        <div class="proxy-hint">纯 A 股宽基指数/联接基金通常保持约 95% 股票仓位，默认即可，无需手动调整。</div>
      </el-form-item>
    </el-form>

    <template #footer>
      <div class="proxy-footer">
        <el-button v-if="hasBinding" type="danger" plain :loading="saving" @click="clearBinding">解除绑定</el-button>
        <div class="proxy-footer-right">
          <el-button @click="emit('update:modelValue', false)">取消</el-button>
          <el-button type="primary" :disabled="!canSave" :loading="saving" @click="save">保存生效</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.proxy-desc { margin: 0 0 14px; font-size: 13px; color: #526071; line-height: 1.55; }
.proxy-desc code { padding: 1px 4px; border-radius: 3px; background: #f1f5f9; font-size: 12px; }
.proxy-hint { margin-top: 6px; font-size: 12px; color: #64748b; }
.proxy-error { margin-top: 6px; font-size: 12px; color: #dc2626; }
.proxy-candidates { list-style: none; margin: 8px 0 0; padding: 0; max-height: 210px; overflow-y: auto; border: 1px solid #e2e8f0; border-radius: 6px; background: #fff; }
.proxy-candidates li + li { border-top: 1px solid #f1f5f9; }
.proxy-candidate-btn { display: flex; align-items: center; justify-content: space-between; gap: 10px; width: 100%; padding: 8px 12px; border: 0; background: transparent; text-align: left; cursor: pointer; }
.proxy-candidate-btn:hover { background: #f8fafc; }
.proxy-candidate-main { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.proxy-candidate-main strong { font-size: 13px; color: #182230; }
.proxy-candidate-code { font-size: 12px; color: #64748b; font-family: ui-monospace, monospace; }
.proxy-selected { margin-bottom: 10px; padding: 8px 10px; border-radius: 6px; background: #eff6ff; color: #1e3a8a; font-size: 13px; }
.proxy-selected-code { margin-left: 4px; font-size: 12px; color: #3b82f6; }
.proxy-advanced-toggle { margin-bottom: 8px; }
.proxy-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; width: 100%; }
.proxy-footer-right { display: flex; gap: 8px; margin-left: auto; }
</style>
