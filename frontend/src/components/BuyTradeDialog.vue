<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { useViewport } from '../utils/viewport'
const props = defineProps<{ securityId: string; accountId?: string; currency?: string; isFund?: boolean }>()
const emit = defineEmits<{ saved: [] }>()
const visible = defineModel<boolean>({ default: false })
const { isMobile } = useViewport()
const saving = ref(false)
const isUS = computed(() => props.currency === 'USD')
const allowDecimalQty = computed(() => Boolean(props.isFund) || isUS.value)
const form = reactive({ qty: '', price: '', executedAt: new Date().toISOString().slice(0, 16), commission: '', stampDuty: '', transferFee: '', otherFee: '' })
/** 提交买入成交记录并在成功后通知父组件刷新。 */
async function submit() {
  saving.value = true
  try { await api.buy({ accountId: props.accountId, securityId: props.securityId, qty: form.qty, price: form.price, executedAt: new Date(form.executedAt).toISOString(), commission: form.commission || '0', stampDuty: form.stampDuty || '0', transferFee: form.transferFee || '0', otherFee: form.otherFee || '0', clientRequestId: crypto.randomUUID() }); ElMessage.success('买入已记账'); visible.value = false; emit('saved') }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '买入失败') }
  finally { saving.value = false }
}
</script>
<template>
  <el-dialog v-model="visible" :title="`录入买入成交（${currency ?? 'CNY'}）`" :width="isMobile ? '94vw' : '460px'">
    <el-form :label-position="isMobile ? 'top' : 'right'" :label-width="isMobile ? undefined : '96px'" class="buy-trade-form">
      <el-form-item :label="isFund ? '确认份额' : '成交数量'"><el-input v-model="form.qty" :inputmode="allowDecimalQty ? 'decimal' : 'numeric'" :placeholder="isFund ? '支持小数份额，如 1234.56' : isUS ? '支持碎股小数，如 0.5' : undefined" /></el-form-item>
      <el-form-item :label="isFund ? '单位净值' : '成交价格'"><el-input v-model="form.price" inputmode="decimal" :placeholder="isFund ? '如 1.2345' : isUS ? '美元单价，如 225.50' : undefined" /></el-form-item>
      <el-form-item label="成交时间"><el-date-picker v-model="form.executedAt" type="datetime" value-format="YYYY-MM-DDTHH:mm" /></el-form-item>
      <el-form-item :label="isFund ? '申购费' : '佣金'"><el-input v-model="form.commission" inputmode="decimal" placeholder="默认 0" /></el-form-item>
      <el-form-item v-if="!isFund" :label="isUS ? '平台/监管费' : '其他费用'"><el-input v-model="form.otherFee" inputmode="decimal" placeholder="默认 0" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submit">保存买入</el-button></template>
  </el-dialog>
</template>
<style scoped>
.buy-trade-form :deep(.el-date-editor) { width: 100%; }
</style>
