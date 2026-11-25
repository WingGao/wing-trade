<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { WeComNotificationUpdate } from '../api/types'
import { useViewport } from '../utils/viewport'

const visible = defineModel<boolean>({ default: false })
const { isMobile } = useViewport()

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const error = ref('')
const configured = ref(false)
const maskedWebhookUrl = ref<string | null>(null)
const form = reactive({
  enabled: false,
  webhookUrl: '',
})
let generation = 0

/** 打开弹窗时按代次加载当前用户脱敏配置，避免过期响应覆盖界面。 */
async function load(): Promise<void> {
  const requestId = ++generation
  loading.value = true
  error.value = ''
  try {
    const config = await api.wecomNotification()
    if (requestId !== generation || !visible.value) return
    configured.value = config.configured
    maskedWebhookUrl.value = config.maskedWebhookUrl
    form.enabled = config.enabled
    form.webhookUrl = ''
  } catch (cause) {
    if (requestId === generation) {
      error.value = cause instanceof Error ? cause.message : '加载推送配置失败'
    }
  } finally {
    if (requestId === generation) loading.value = false
  }
}

/** 保存企微推送配置；已配置且输入框留空时保留原密钥，支持显式清除。 */
async function submit(clear = false): Promise<void> {
  if (saving.value || testing.value || loading.value || error.value) return
  saving.value = true
  try {
    const payload: WeComNotificationUpdate = { enabled: clear ? false : form.enabled }
    if (clear) {
      payload.webhookUrl = null
    } else if (form.webhookUrl.trim()) {
      payload.webhookUrl = form.webhookUrl.trim()
    }
    const saved = await api.saveWecomNotification(payload)
    configured.value = saved.configured
    maskedWebhookUrl.value = saved.maskedWebhookUrl
    form.enabled = saved.enabled
    form.webhookUrl = ''
    ElMessage.success(clear ? '企微推送配置已清除' : '推送设置已保存')
    visible.value = false
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '保存推送设置失败')
  } finally {
    saving.value = false
  }
}

/** 发送测试消息，优先使用输入框中未保存的新地址，留空则测试已保存地址。 */
async function sendTest(): Promise<void> {
  if (testing.value || saving.value || loading.value || error.value) return
  testing.value = true
  try {
    const raw = form.webhookUrl.trim()
    const result = await api.testWecomNotification(raw || undefined)
    ElMessage.success(result.message || '测试消息已发送')
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '发送测试消息失败')
  } finally {
    testing.value = false
  }
}

watch(
  visible,
  (open) => {
    if (open) void load()
    else generation += 1
  },
  { immediate: true },
)
</script>

<template>
  <el-dialog v-model="visible" title="止盈推送设置（企业微信）" :width="isMobile ? '94vw' : '500px'">
    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 12px" />
    <p class="muted">
      通过企业微信群机器人 Webhook 推送盘中止盈提醒（同批次同目标按自然日去重，同轮多笔自动合并）。
    </p>
    <el-form
      v-loading="loading"
      :disabled="loading || saving || testing || !!error"
      :label-position="isMobile ? 'top' : 'right'"
      :label-width="isMobile ? undefined : '116px'"
    >
      <el-form-item label="启用止盈推送">
        <el-switch v-model="form.enabled" />
      </el-form-item>
      <el-form-item label="企微 Webhook">
        <el-input
          v-model="form.webhookUrl"
          type="password"
          show-password
          autocomplete="off"
          :placeholder="configured ? '留空保留当前已保存地址，输入新地址覆盖' : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...'"
        />
        <div v-if="configured && maskedWebhookUrl" class="webhook-hint">
          当前已配置：<code>{{ maskedWebhookUrl }}</code>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <div class="dialog-footer-actions">
        <el-button v-if="configured" :disabled="loading || saving || testing || !!error" @click="submit(true)">
          清除配置
        </el-button>
        <el-button
          :disabled="loading || saving || !!error || (!configured && !form.webhookUrl.trim())"
          :loading="testing"
          @click="sendTest"
        >
          发送测试
        </el-button>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :disabled="loading || testing || !!error" :loading="saving" @click="submit()">
          保存设置
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.webhook-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #64748b;
  word-break: break-all;
}
.dialog-footer-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
</style>
