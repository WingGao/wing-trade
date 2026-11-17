<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Lot } from '../api/types'
import { formatQuantity, formatTargetPercent, percentInputToRate, rateToPercentInput } from '../utils/format'
const props = defineProps<{ lotId: string; lot?: Lot }>()
const emit = defineEmits<{ saved: [] }>()
const visible = defineModel<boolean>({ default: false })
const saving = ref(false)
const selectedTargetId = ref('')
const form = reactive({ plannedSellQty: '', targetReturnPct: '', calculationBasis: 'NET', alertEnabled: true })
const activeTargets = computed(() => (props.lot?.targets ?? []).filter((target) => !['CANCELLED', 'COMPLETED'].includes(target.status)))

/** 按选中的明确目标填表；没有覆盖时保持收益率空白以表达继承。 */
function fillForm() {
  const target = activeTargets.value.find((item) => item.id === selectedTargetId.value)
  form.plannedSellQty = (target?.plannedSellQty ?? props.lot?.remainingQty ?? '').replace(/\.0+$/, '')
  form.targetReturnPct = target ? rateToPercentInput(target.targetReturnPct) : ''
  form.calculationBasis = target?.calculationBasis ?? 'NET'
  form.alertEnabled = target?.alertEnabled ?? true
}

/** 清除所有本批次有效覆盖，继承目标仍由后端动态投影。 */
async function restoreInherited() {
  if (saving.value) return
  saving.value = true
  try {
    for (const target of activeTargets.value) await api.deleteTarget(target.id)
    ElMessage.success('已恢复继承个股或全局止盈')
    visible.value = false
    emit('saved')
  } catch (cause) { ElMessage.error(cause instanceof Error ? cause.message : '恢复继承失败'); emit('saved') }
  finally { saving.value = false }
}

/** 新增或更新用户明确填写的目标；空值恢复继承而不写入继承副本。 */
async function submit() {
  if (!form.targetReturnPct.trim()) { await restoreInherited(); return }
  if (saving.value) return
  saving.value = true
  try {
    const payload = { ...form, targetReturnPct: percentInputToRate(form.targetReturnPct) }
    if (selectedTargetId.value) await api.updateTarget(selectedTargetId.value, payload)
    else await api.createTarget(props.lotId, payload)
    ElMessage.success('止盈目标已保存')
    visible.value = false
    emit('saved')
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '目标保存失败') }
  finally { saving.value = false }
}

watch(visible, (open) => { if (open) { selectedTargetId.value = activeTargets.value[0]?.id ?? ''; fillForm() } }, { immediate: true })
watch(selectedTargetId, fillForm)
</script>
<template>
  <el-dialog v-model="visible" title="设置批次止盈目标" width="470px">
    <p class="muted">剩余 {{ formatQuantity(lot?.remainingQty) }} 股。收益率留空继承个股或全局；继承值不会保存为批次目标。</p>
    <p v-if="lot?.effectiveTarget && lot.effectiveTarget.source !== 'LOT'" class="muted">当前继承：{{ lot.effectiveTarget.source === 'SECURITY' ? '个股' : '全局' }} {{ formatTargetPercent(lot.effectiveTarget.targetReturnPct) }}</p>
    <el-form :disabled="saving" label-width="110px">
      <el-form-item v-if="activeTargets.length" label="分段目标"><el-select v-model="selectedTargetId"><el-option v-for="target in activeTargets" :key="target.id" :value="target.id" :label="`${formatTargetPercent(target.targetReturnPct)} · ${formatQuantity(target.plannedSellQty)} 股`" /><el-option value="" label="新增分段目标" /></el-select></el-form-item>
      <el-form-item label="计划卖出股数"><el-input v-model="form.plannedSellQty" inputmode="numeric" /></el-form-item>
      <el-form-item label="目标收益率"><el-input v-model="form.targetReturnPct" placeholder="留空继承个股 / 全局" inputmode="decimal"><template #append>%</template></el-input></el-form-item>
      <el-form-item label="计算口径"><el-radio-group v-model="form.calculationBasis"><el-radio value="GROSS">毛收益</el-radio><el-radio value="NET">净收益</el-radio></el-radio-group></el-form-item>
      <el-form-item label="提醒"><el-switch v-model="form.alertEnabled" /></el-form-item>
    </el-form>
    <template #footer><el-button :disabled="saving" @click="restoreInherited">恢复继承</el-button><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submit">保存目标</el-button></template>
  </el-dialog>
</template>
