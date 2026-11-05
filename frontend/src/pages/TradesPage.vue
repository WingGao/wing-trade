<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { Trade } from '../api/types'
const trades = ref<Trade[]>([]); const loading = ref(false); const side = ref('')
const filtered = () => side.value ? trades.value.filter((trade) => trade.side === side.value) : trades.value
async function load() { loading.value = true; try { trades.value = await api.trades() } finally { loading.value = false } }
async function voidTrade(trade: Trade) { try { const result = await ElMessageBox.prompt('请输入撤销原因', '撤销交易', { inputPlaceholder: '例如：录入价格错误' }); await api.voidTrade(trade.id, result.value); ElMessage.success('交易已撤销'); await load() } catch { /* cancel */ } }
onMounted(load)
</script>
<template><section class="page-head"><div><h2>交易流水</h2><p class="muted">所有成交可追溯，撤销会保留审计记录。</p></div><el-select v-model="side" clearable placeholder="全部方向" style="width:150px"><el-option label="买入" value="BUY" /><el-option label="卖出" value="SELL" /></el-select></section><el-card class="panel"><el-table :data="filtered()" v-loading="loading" stripe empty-text="暂无交易"><el-table-column prop="executedAt" label="成交时间" min-width="170" /><el-table-column prop="securityId" label="证券" /><el-table-column label="方向"><template #default="{ row }"><el-tag :type="row.side === 'BUY' ? 'danger' : 'success'">{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag></template></el-table-column><el-table-column prop="qty" label="数量" /><el-table-column prop="price" label="价格" /><el-table-column prop="feesTotal" label="费用" /><el-table-column prop="status" label="状态" /><el-table-column label="操作"><template #default="{ row }"><el-button link type="danger" :disabled="row.status !== 'ACTIVE'" @click="voidTrade(row)">撤销</el-button></template></el-table-column></el-table></el-card></template>
