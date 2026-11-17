<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { formatTargetPercent, percentInputToRate, rateToPercentInput } from '../utils/format'

const props = defineProps<{ securityId?: string }>()
const emit = defineEmits<{ saved: [] }>()
const visible = defineModel<boolean>({ default: false })
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const inheritedLabel = ref('')
const form = reactive({ targetReturnPct: '', calculationBasis: 'NET' as 'NET' | 'GROSS', alertEnabled: true })
const title = computed(() => props.securityId ? '个股止盈设置' : '全局止盈设置')
let generation = 0

/** 每次打开只读取本层设置，继承值用于提示而不伪装为本层覆盖。 */
async function load() {
  const request = ++generation
  loading.value = true
  error.value = ''
  try {
    const setting = await api.profitSetting(props.securityId)
    const parent = props.securityId ? await api.profitSetting() : null
    if (request !== generation || !visible.value) return
    form.targetReturnPct = setting ? rateToPercentInput(setting.targetReturnPct) : ''
    form.calculationBasis = setting?.calculationBasis ?? 'NET'
    form.alertEnabled = setting?.alertEnabled ?? true
    inheritedLabel.value = parent ? `全局目标 ${formatTargetPercent(parent.targetReturnPct)}` : '全局未设置止盈'
  } catch (cause) { if (request === generation) error.value = cause instanceof Error ? cause.message : '加载止盈设置失败' }
  finally { if (request === generation) loading.value = false }
}

/** 保存明确覆盖，留空时清除本层设置以恢复继承。 */
async function submit(clear = false) {
  if (saving.value || loading.value || error.value) return
  saving.value = true
  try {
    const raw = clear ? '' : form.targetReturnPct.trim()
    await api.saveProfitSetting({ targetReturnPct: raw ? percentInputToRate(raw) : null, calculationBasis: form.calculationBasis, alertEnabled: form.alertEnabled }, props.securityId)
    ElMessage.success(raw ? '止盈设置已保存' : props.securityId ? '已恢复继承全局止盈' : '全局止盈设置已清除')
    visible.value = false
    emit('saved')
  } catch (cause) { ElMessage.error(cause instanceof Error ? cause.message : '保存止盈失败') }
  finally { saving.value = false }
}

watch(() => [visible.value, props.securityId] as const, ([open]) => { if (open) void load(); else generation += 1 }, { immediate: true })
</script>

<template>
  <el-dialog v-model="visible" :title="title" width="460px">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p class="muted">{{ securityId ? `留空继承全局设置；${inheritedLabel}。` : '没有个股或批次覆盖时使用此设置。留空可清除全局默认值。' }}</p>
    <el-form v-loading="loading" :disabled="loading || saving || !!error" label-width="105px">
      <el-form-item label="目标收益率"><el-input v-model="form.targetReturnPct" placeholder="留空使用上级设置" inputmode="decimal"><template #append>%</template></el-input></el-form-item>
      <el-form-item label="计算口径"><el-radio-group v-model="form.calculationBasis"><el-radio value="GROSS">毛收益</el-radio><el-radio value="NET">净收益</el-radio></el-radio-group></el-form-item>
      <el-form-item label="提醒"><el-switch v-model="form.alertEnabled" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="loading || saving || !!error" @click="submit(true)">{{ securityId ? '恢复继承' : '清除设置' }}</el-button>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :disabled="loading || !!error" :loading="saving" @click="submit()">保存设置</el-button>
    </template>
  </el-dialog>
</template>
