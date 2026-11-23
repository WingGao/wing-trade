<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { Account, Currency } from '../api/types'
import { useAccountsStore } from '../stores/accounts'
import { useViewport } from '../utils/viewport'

const props = defineProps<{ defaultCurrency?: Currency }>()
const emit = defineEmits<{ created: [account: Account] }>()
const visible = defineModel<boolean>({ default: false })
const accountsStore = useAccountsStore()
const { isMobile } = useViewport()
const saving = ref(false)
const form = reactive<{ name: string; currency: Currency; broker: string }>({
  name: '',
  currency: 'USD',
  broker: '',
})

/** 打开弹窗时按默认币种初始化表单。 */
watch(visible, (open) => {
  if (!open) return
  const currency = props.defaultCurrency ?? 'USD'
  form.currency = currency
  form.name = currency === 'USD' ? '美股账户' : 'A股账户'
  form.broker = ''
})

/** 校验并提交新建资金账户请求。 */
async function submit(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请输入账户名称')
    return
  }
  saving.value = true
  try {
    const created = await accountsStore.createAccount({
      name: form.name,
      currency: form.currency,
      broker: form.broker || null,
    })
    ElMessage.success(`已创建 ${created.currency} 账户「${created.name}」`)
    visible.value = false
    emit('created', created)
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '创建账户失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="新建资金账户" :width="isMobile ? '94vw' : '420px'">
    <el-form :label-position="isMobile ? 'top' : 'right'" :label-width="isMobile ? undefined : '82px'">
      <el-form-item label="结算币种">
        <el-radio-group v-model="form.currency">
          <el-radio-button value="USD">USD 美元（美股）</el-radio-button>
          <el-radio-button value="CNY">CNY 人民币（A股/基金）</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="账户名称">
        <el-input v-model="form.name" maxlength="64" placeholder="例如：盈透美股 / 招商证券" />
      </el-form-item>
      <el-form-item label="券商（选填）">
        <el-input v-model="form.broker" maxlength="64" placeholder="例如：IBKR / 富途 / 华泰" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">创建并切换</el-button>
    </template>
  </el-dialog>
</template>
