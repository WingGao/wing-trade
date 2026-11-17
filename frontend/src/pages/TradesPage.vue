<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { Security, Trade } from '../api/types'
import { formatDateTime, formatDecimal, formatQuantity } from '../utils/format'
import EditTradeDialog from '../components/EditTradeDialog.vue'
const trades = ref<Trade[]>([])
const securities = ref<Security[]>([])
const loading = ref(false)
const side = ref('')
const error = ref('')
const editingTrade = ref<Trade | null>(null)
const editVisible = ref(false)
let alive = true
let loadGeneration = 0
const securityMap = computed(() => new Map(securities.value.map((security) => [security.id, security])))
const filtered = computed(() => trades.value.filter((trade) => !side.value || trade.side === side.value).sort((a, b) => new Date(b.executedAt).getTime() - new Date(a.executedAt).getTime() || b.id.localeCompare(a.id)))

/** 优先展示流水内证券信息，兼容旧响应时用本地证券目录补全。 */
function securityLabel(trade: Trade): { code: string; name: string } {
  const security = securityMap.value.get(trade.securityId)
  const market = trade.market ?? security?.market
  const symbol = trade.symbol ?? security?.symbol
  return { code: symbol ? `${market ? `${market}:` : ''}${symbol}` : '证券信息不可用', name: trade.name ?? security?.name ?? '' }
}

/** 独立处理目录失败，避免辅助证券名称加载阻塞原始交易流水。 */
async function load() {
  const request = ++loadGeneration
  loading.value = true
  error.value = ''
  try {
    const [result, directory] = await Promise.allSettled([api.trades(), api.securities()])
    if (!alive || request !== loadGeneration) return false
    if (result.status === 'rejected') throw result.reason
    trades.value = result.value
    if (directory.status === 'fulfilled') securities.value = directory.value
    return true
  } catch (cause) { if (alive && request === loadGeneration) error.value = cause instanceof Error ? cause.message : '加载流水失败'; return false }
  finally { if (alive && request === loadGeneration) loading.value = false }
}

/** 旧响应缺少精确费用或版本时不猜测补零，保留只读兼容。 */
function canEdit(trade: Trade): boolean {
  return trade.status === 'ACTIVE' && Number.isInteger(trade.revision) && ['commission', 'stampDuty', 'transferFee', 'otherFee'].every((key) => typeof trade[key as keyof Trade] === 'string')
}

/** 将编辑锚定至选定的原始流水。 */
function editTrade(trade: Trade) {
  editingTrade.value = trade
  editVisible.value = true
}

/** 保存已提交后独立报告列表重读失败，避免误认为编辑已回滚。 */
async function reloadAfterEdit() {
  if (!await load() && alive) ElMessage.warning('交易已保存，流水列表读取失败，请重新加载页面')
}

/** 保留撤销原因确认并明确展示接口失败，成功后重载流水。 */
async function voidTrade(trade: Trade) {
  let reason: string
  try { reason = (await ElMessageBox.prompt('请输入撤销原因', '撤销交易', { inputPlaceholder: '例如：录入价格错误' })).value } catch { return }
  try { await api.voidTrade(trade.id, reason); ElMessage.success('交易已撤销'); await load() }
  catch (cause) { ElMessage.error(cause instanceof Error ? cause.message : '撤销失败') }
}
onMounted(load)
onBeforeUnmount(() => { alive = false; loadGeneration += 1 })
</script>
<template><section class="page-head"><div><h2>交易流水</h2><p class="muted">所有成交可追溯，编辑和撤销均保留审计记录。</p></div><el-select v-model="side" clearable placeholder="全部方向" style="width:150px"><el-option label="买入" value="BUY" /><el-option label="卖出" value="SELL" /></el-select></section><el-alert v-if="error" :title="error" type="error" :closable="false" class="status-alert" /><el-card class="panel"><el-table :data="filtered" v-loading="loading" stripe empty-text="暂无交易" class="trades-table"><el-table-column label="成交时间" min-width="170"><template #default="{ row }">{{ formatDateTime(row.executedAt) }}</template></el-table-column><el-table-column label="证券" min-width="180"><template #default="{ row }"><div :title="`证券 ID：${row.securityId}`"><strong>{{ securityLabel(row).code }}</strong><div class="muted">{{ securityLabel(row).name }}</div></div></template></el-table-column><el-table-column label="方向"><template #default="{ row }"><el-tag :type="row.side === 'BUY' ? 'danger' : 'success'">{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag></template></el-table-column><el-table-column label="数量"><template #default="{ row }">{{ formatQuantity(row.qty) }}</template></el-table-column><el-table-column label="价格"><template #default="{ row }">{{ formatDecimal(row.price, 3) }}</template></el-table-column><el-table-column label="费用"><template #default="{ row }">{{ formatDecimal(row.feesTotal) }}</template></el-table-column><el-table-column prop="status" label="状态" /><el-table-column label="操作" min-width="120"><template #default="{ row }"><el-button link type="primary" :disabled="!canEdit(row)" @click="editTrade(row)">编辑</el-button><el-button link type="danger" :disabled="row.status !== 'ACTIVE'" @click="voidTrade(row)">撤销</el-button></template></el-table-column></el-table></el-card><EditTradeDialog v-model="editVisible" :trade="editingTrade" :security-label="editingTrade ? `${securityLabel(editingTrade).code} ${securityLabel(editingTrade).name}` : ''" @saved="reloadAfterEdit" /></template>
