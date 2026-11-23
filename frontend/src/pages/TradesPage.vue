<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { Security, Trade } from '../api/types'
import { useAccountsStore } from '../stores/accounts'
import { formatDateTime, formatMoney, formatQuantity, isFundSecurity, priceDigits, quantityUnit } from '../utils/format'
import { useViewport } from '../utils/viewport'
import EditTradeDialog from '../components/EditTradeDialog.vue'
const { isMobile } = useViewport()
const accountsStore = useAccountsStore()
const trades = ref<Trade[]>([])
const securities = ref<Security[]>([])
const loading = ref(false)
const side = ref('')
const accountFilter = ref<string>('__ACTIVE__')
const error = ref('')
const editingTrade = ref<Trade | null>(null)
const editVisible = ref(false)
let alive = true
let loadGeneration = 0
const securityMap = computed(() => new Map(securities.value.map((security) => [security.id, security])))
const effectiveAccountId = computed<string | undefined>(() => {
  if (accountFilter.value === '__ALL__') return undefined
  if (accountFilter.value === '__ACTIVE__') return accountsStore.activeAccountId || undefined
  return accountFilter.value || undefined
})
const filtered = computed(() => trades.value.filter((trade) => !side.value || trade.side === side.value).sort((a, b) => new Date(b.executedAt).getTime() - new Date(a.executedAt).getTime() || b.id.localeCompare(a.id)))

/** 返回流水对应的证券特征对象，用于基金份额与美股碎股格式化。 */
function tradeSecurityMeta(trade: Trade): { market?: string | null; securityType?: string | null; currency?: string | null } {
  const security = securityMap.value.get(trade.securityId)
  return {
    market: trade.market ?? security?.market,
    securityType: trade.securityType ?? security?.securityType,
    currency: trade.currency ?? security?.currency,
  }
}

/** 判断流水关联证券是否为场外基金，优先读流水字段并兼容目录回退。 */
function isFundTrade(trade: Trade): boolean {
  return isFundSecurity(tradeSecurityMeta(trade))
}

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
    const [result, directory] = await Promise.allSettled([api.trades(effectiveAccountId.value), api.securities()])
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
watch(effectiveAccountId, () => { if (alive) void load() })
onMounted(() => { void accountsStore.loadAccounts(); void load() })
onBeforeUnmount(() => { alive = false; loadGeneration += 1 })
</script>
<template>
  <template v-if="!isMobile">
    <section class="page-head">
      <div><h2>交易流水</h2><p class="muted">所有成交可追溯，编辑和撤销均保留审计记录。</p></div>
      <div class="toolbar">
        <el-select v-model="accountFilter" style="width:185px" placeholder="账户范围">
          <el-option label="当前账户" value="__ACTIVE__" />
          <el-option label="全部账户" value="__ALL__" />
          <el-option v-for="account in accountsStore.accounts" :key="account.id" :label="`${account.name} (${account.currency})`" :value="account.id" />
        </el-select>
        <el-select v-model="side" clearable placeholder="全部方向" style="width:130px"><el-option label="买入" value="BUY" /><el-option label="卖出" value="SELL" /></el-select>
      </div>
    </section>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="status-alert" />
    <el-card class="panel"><el-table :data="filtered" v-loading="loading" stripe empty-text="暂无交易" class="trades-table"><el-table-column label="成交时间" min-width="170"><template #default="{ row }">{{ formatDateTime(row.executedAt) }}</template></el-table-column><el-table-column label="证券" min-width="180"><template #default="{ row }"><div :title="`证券 ID：${row.securityId}`"><strong>{{ securityLabel(row).code }}</strong><div class="muted">{{ securityLabel(row).name }}</div></div></template></el-table-column><el-table-column label="方向"><template #default="{ row }"><el-tag :type="row.side === 'BUY' ? 'danger' : 'success'">{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag></template></el-table-column><el-table-column label="数量"><template #default="{ row }">{{ formatQuantity(row.qty, tradeSecurityMeta(row)) }} {{ quantityUnit(isFundTrade(row)) }}</template></el-table-column><el-table-column label="价格"><template #default="{ row }">{{ formatMoney(row.price, row.currency, priceDigits(isFundTrade(row), 3)) }}</template></el-table-column><el-table-column label="费用"><template #default="{ row }">{{ formatMoney(row.feesTotal, row.currency) }}</template></el-table-column><el-table-column prop="status" label="状态" /><el-table-column label="操作" min-width="120"><template #default="{ row }"><el-button link type="primary" :disabled="!canEdit(row)" @click="editTrade(row)">编辑</el-button><el-button link type="danger" :disabled="row.status !== 'ACTIVE'" @click="voidTrade(row)">撤销</el-button></template></el-table-column></el-table></el-card>
  </template>
  <template v-else>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="status-alert" />
    <section class="m-section" v-loading="loading">
      <div class="m-section-head">
        <span>交易流水 <span class="m-section-sub">{{ filtered.length }} 条</span></span>
        <div style="display:flex;gap:6px;align-items:center">
          <el-select v-model="accountFilter" size="small" style="width:105px">
            <el-option label="当前账户" value="__ACTIVE__" />
            <el-option label="全部账户" value="__ALL__" />
          </el-select>
          <el-select v-model="side" clearable size="small" placeholder="方向" style="width:86px">
            <el-option label="买入" value="BUY" />
            <el-option label="卖出" value="SELL" />
          </el-select>
        </div>
      </div>
      <div v-if="filtered.length" class="m-trade-list">
        <article v-for="row in filtered" :key="row.id" class="m-trade-item">
          <div class="m-trade-top">
            <div class="m-trade-sec" :title="`证券 ID：${row.securityId}`">
              <el-tag :type="row.side === 'BUY' ? 'danger' : 'success'" size="small">{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag>
              <strong>{{ securityLabel(row).code }}</strong>
              <span v-if="securityLabel(row).name" class="m-trade-name">{{ securityLabel(row).name }}</span>
            </div>
            <span class="m-trade-status">{{ row.status }}</span>
          </div>
          <div class="m-trade-mid">
            <div class="m-trade-nums num">
              <strong>{{ formatQuantity(row.qty, tradeSecurityMeta(row)) }} {{ quantityUnit(isFundTrade(row)) }} × {{ formatMoney(row.price, row.currency, priceDigits(isFundTrade(row), 3)) }}</strong>
              <span class="m-trade-fee">费用 {{ formatMoney(row.feesTotal, row.currency) }} · {{ formatDateTime(row.executedAt) }}</span>
            </div>
            <div class="m-trade-ops">
              <el-button link type="primary" size="small" :disabled="!canEdit(row)" @click="editTrade(row)">编辑</el-button>
              <el-button link type="danger" size="small" :disabled="row.status !== 'ACTIVE'" @click="voidTrade(row)">撤销</el-button>
            </div>
          </div>
        </article>
      </div>
      <div v-else class="m-empty">暂无交易</div>
    </section>
  </template>
  <EditTradeDialog v-model="editVisible" :trade="editingTrade" :security-label="editingTrade ? `${securityLabel(editingTrade).code} ${securityLabel(editingTrade).name}` : ''" @saved="reloadAfterEdit" />
</template>
<style scoped>
.m-trade-list { display: flex; flex-direction: column; }
.m-trade-item { padding: 8px 10px; border-bottom: 1px solid #edf0f5; }
.m-trade-item:last-child { border-bottom: 0; }
.m-trade-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 5px; }
.m-trade-sec { display: flex; align-items: center; gap: 5px; min-width: 0; font-size: 13px; }
.m-trade-name { font-size: 11px; color: #64748b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.m-trade-status { font-size: 11px; color: #7d899b; flex-shrink: 0; }
.m-trade-mid { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.m-trade-nums { display: flex; flex-direction: column; gap: 2px; min-width: 0; font-size: 12px; color: #182230; }
.m-trade-fee { font-size: 11px; color: #7d899b; }
.m-trade-ops { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
</style>
