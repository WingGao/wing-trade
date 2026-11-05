<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Lot, SellPreview } from '../api/types'
const props = defineProps<{ securityId: string; lots: Lot[] }>()
const emit = defineEmits<{ saved: [] }>()
const visible = defineModel<boolean>({ default: false })
const form = reactive({ qty: '', price: '', executedAt: new Date().toISOString().slice(0, 16) })
const preview = ref<SellPreview | null>(null); const loading = ref(false); const submitting = ref(false)
const matches = computed(() => preview.value?.matches ?? [])
async function previewSell() { loading.value = true; try { preview.value = await api.previewSell({ securityId: props.securityId, qty: form.qty, price: form.price, executedAt: new Date(form.executedAt).toISOString() }) } catch (error) { ElMessage.error(error instanceof Error ? error.message : '预览失败') } finally { loading.value = false } }
async function submit() { submitting.value = true; try { await api.sell({ securityId: props.securityId, qty: form.qty, price: form.price, executedAt: new Date(form.executedAt).toISOString(), clientRequestId: crypto.randomUUID(), matches: matches.value.map((match) => ({ lotId: match.lotId, matchedQty: match.matchedQty })) }); ElMessage.success('卖出已记账'); visible.value = false; emit('saved') } catch (error) { ElMessage.error(error instanceof Error ? error.message : '卖出失败') } finally { submitting.value = false } }
</script>
<template>
  <el-drawer v-model="visible" title="卖出配对工作台" size="520px"><el-form label-width="86px"><el-form-item label="卖出数量"><el-input v-model="form.qty" /></el-form-item><el-form-item label="卖出价格"><el-input v-model="form.price" /></el-form-item><el-form-item label="成交时间"><el-date-picker v-model="form.executedAt" type="datetime" value-format="YYYY-MM-DDTHH:mm" /></el-form-item></el-form><el-button type="primary" :loading="loading" @click="previewSell">预览 FIFO 配对</el-button><el-divider /><div v-if="preview" class="preview"><p>预计净回款 <strong>{{ preview.cashIn }}</strong> · 已实现收益 <strong>{{ preview.realizedPnl }}</strong></p><el-table :data="matches" size="small"><el-table-column prop="lotId" label="批次" /><el-table-column prop="matchedQty" label="配对股数" /><el-table-column prop="allocatedBuyCost" label="分摊成本" /></el-table></div><template #footer><el-button @click="visible = false">取消</el-button><el-button type="danger" :loading="submitting" :disabled="!preview" @click="submit">确认卖出</el-button></template></el-drawer>
</template>
