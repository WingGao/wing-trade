<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
const props = defineProps<{ lotId: string }>(); const emit = defineEmits<{ saved: [] }>()
const visible = defineModel<boolean>({ default: false }); const saving = ref(false)
const form = reactive({ plannedSellQty: '', targetReturnPct: '', calculationBasis: 'NET', alertEnabled: true })
async function submit() { saving.value = true; try { await api.createTarget(props.lotId, form); ElMessage.success('止盈目标已保存'); visible.value = false; emit('saved') } catch (error) { ElMessage.error(error instanceof Error ? error.message : '目标保存失败') } finally { saving.value = false } }
</script>
<template><el-dialog v-model="visible" title="设置分段止盈目标" width="430px"><el-form label-width="110px"><el-form-item label="计划卖出股数"><el-input v-model="form.plannedSellQty" /></el-form-item><el-form-item label="目标收益率"><el-input v-model="form.targetReturnPct"><template #append>%</template></el-input></el-form-item><el-form-item label="计算口径"><el-radio-group v-model="form.calculationBasis"><el-radio value="GROSS">毛收益</el-radio><el-radio value="NET">净收益</el-radio></el-radio-group></el-form-item><el-form-item label="提醒"><el-switch v-model="form.alertEnabled" /></el-form-item></el-form><template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submit">保存目标</el-button></template></el-dialog></template>
