<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
const props = defineProps<{ securityId: string }>()
const emit = defineEmits<{ saved: [] }>()
const visible = defineModel<boolean>({ default: false })
const saving = ref(false)
const form = reactive({ qty: '', price: '', executedAt: new Date().toISOString().slice(0, 16), commission: '', stampDuty: '', transferFee: '' })
async function submit() {
  saving.value = true
  try { await api.buy({ securityId: props.securityId, qty: form.qty, price: form.price, executedAt: new Date(form.executedAt).toISOString(), commission: form.commission || '0', stampDuty: form.stampDuty || '0', transferFee: form.transferFee || '0', clientRequestId: crypto.randomUUID() }); ElMessage.success('买入已记账'); visible.value = false; emit('saved') }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '买入失败') }
  finally { saving.value = false }
}
</script>
<template>
  <el-dialog v-model="visible" title="录入买入成交" width="460px"><el-form label-width="90px"><el-form-item label="成交数量"><el-input v-model="form.qty" /></el-form-item><el-form-item label="成交价格"><el-input v-model="form.price" /></el-form-item><el-form-item label="成交时间"><el-date-picker v-model="form.executedAt" type="datetime" value-format="YYYY-MM-DDTHH:mm" /></el-form-item><el-form-item label="佣金"><el-input v-model="form.commission" /></el-form-item></el-form><template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submit">保存买入</el-button></template></el-dialog>
</template>
