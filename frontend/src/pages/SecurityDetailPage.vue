<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Lot, Security } from '../api/types'
import LotTable from '../components/LotTable.vue'; import BuyTradeDialog from '../components/BuyTradeDialog.vue'; import SellMatchDrawer from '../components/SellMatchDrawer.vue'; import ProfitTargetDialog from '../components/ProfitTargetDialog.vue'
import { usePreferencesStore } from '../stores/preferences'
const route = useRoute(); const preferences = usePreferencesStore(); const security = ref<Security | null>(null); const lots = ref<Lot[]>([]); const closedLotCount = ref(0); const realizedPnl = ref('0'); const loading = ref(false); const buyVisible = ref(false); const sellVisible = ref(false); const targetVisible = ref(false); const selectedLot = ref<Lot | null>(null)
const securityId = computed(() => security.value?.id ?? String(route.params.symbol))
async function load() { loading.value = true; try { const found = await api.securities(String(route.params.symbol)); security.value = found.find((item) => item.market === String(route.params.market).toUpperCase() || item.symbol === String(route.params.symbol)) ?? found[0] ?? null; if (security.value) { const result = await api.lots(security.value.id, preferences.includeClosed); lots.value = result.lots; closedLotCount.value = result.closedLotCount; realizedPnl.value = result.realizedPnl } } catch (error) { ElMessage.error(error instanceof Error ? error.message : '加载个股失败') } finally { loading.value = false } }
onMounted(load)
function addTarget(lot: Lot) { selectedLot.value = lot; targetVisible.value = true }
</script>
<template>
  <section class="page-head"><div><h2>{{ route.params.market }}:{{ route.params.symbol }}</h2><p class="muted">批次、配对和分段止盈计划</p></div><div class="toolbar"><el-switch v-model="preferences.includeClosed" active-text="显示已结清" @change="load" /><el-button @click="buyVisible = true" :disabled="!security">录入买入</el-button><el-button type="danger" @click="sellVisible = true" :disabled="!security || !lots.length">卖出配对</el-button></div></section>
  <el-card class="panel"><template #header><div class="panel-title">持仓批次 <span class="muted">已结清 {{ closedLotCount }} 批 · 累计已实现收益 ¥{{ realizedPnl }}</span></div></template><div v-loading="loading"><LotTable :lots="lots" :include-closed="preferences.includeClosed" /><div v-if="lots.length" class="table-actions"><el-button v-for="lot in lots.filter((item) => item.status === 'OPEN')" :key="lot.id" link type="primary" @click="addTarget(lot)">为批次 {{ lot.id.slice(0, 8) }} 设置止盈</el-button></div></div></el-card>
  <BuyTradeDialog v-if="security" v-model="buyVisible" :security-id="security.id" @saved="load" /><SellMatchDrawer v-if="security" v-model="sellVisible" :security-id="security.id" :lots="lots" @saved="load" /><ProfitTargetDialog v-if="selectedLot" v-model="targetVisible" :lot-id="selectedLot.id" @saved="load" />
</template>
