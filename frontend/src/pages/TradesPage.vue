<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { Security, Trade } from '../api/types'
import { formatDateTime, formatDecimal, formatQuantity } from '../utils/format'
const trades = ref<Trade[]>([])
const securities = ref<Security[]>([])
const loading = ref(false)
const side = ref('')
const error = ref('')
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
  loading.value = true
  error.value = ''
  try {
    const [result, directory] = await Promise.allSettled([api.trades(), api.securities()])
    if (result.status === 'rejected') throw result.reason
    trades.value = result.value
    if (directory.status === 'fulfilled') securities.value = directory.value
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '加载流水失败' }
  finally { loading.value = false }
}

/** 保留撤销原因确认并明确展示接口失败，成功后重载流水。 */
async function voidTrade(trade: Trade) {
  let reason: string
  try { reason = (await ElMessageBox.prompt('请输入撤销原因', '撤销交易', { inputPlaceholder: '例如：录入价格错误' })).value } catch { return }
  try { await api.voidTrade(trade.id, reason); ElMessage.success('交易已撤销'); await load() }
  catch (cause) { ElMessage.error(cause instanceof Error ? cause.message : '撤销失败') }
}
onMounted(load)
</script>
<template><section class="page-head"><div><h2>交易流水</h2><p class="muted">所有成交可追溯，撤销会保留审计记录。</p></div><el-select v-model="side" clearable placeholder="全部方向" style="width:150px"><el-option label="买入" value="BUY" /><el-option label="卖出" value="SELL" /></el-select></section><el-alert v-if="error" :title="error" type="error" :closable="false" class="status-alert" /><el-card class="panel"><el-table :data="filtered" v-loading="loading" stripe empty-text="暂无交易" class="trades-table"><el-table-column label="成交时间" min-width="170"><template #default="{ row }">{{ formatDateTime(row.executedAt) }}</template></el-table-column><el-table-column label="证券" min-width="180"><template #default="{ row }"><div :title="`证券 ID：${row.securityId}`"><strong>{{ securityLabel(row).code }}</strong><div class="muted">{{ securityLabel(row).name }}</div></div></template></el-table-column><el-table-column label="方向"><template #default="{ row }"><el-tag :type="row.side === 'BUY' ? 'danger' : 'success'">{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag></template></el-table-column><el-table-column label="数量"><template #default="{ row }">{{ formatQuantity(row.qty) }}</template></el-table-column><el-table-column label="价格"><template #default="{ row }">{{ formatDecimal(row.price, 3) }}</template></el-table-column><el-table-column label="费用"><template #default="{ row }">{{ formatDecimal(row.feesTotal) }}</template></el-table-column><el-table-column prop="status" label="状态" /><el-table-column label="操作"><template #default="{ row }"><el-button link type="danger" :disabled="row.status !== 'ACTIVE'" @click="voidTrade(row)">撤销</el-button></template></el-table-column></el-table></el-card></template>
