<template>
  <div class="auth-screen">
    <el-card class="auth-card" shadow="always">
      <div class="auth-brand">
        <span class="brand-mark">翼</span>
        <div>
          <strong>交易账本</strong>
          <small>Portfolio Journal · 受控多用户工作台</small>
        </div>
      </div>

      <h2 class="auth-title">邮箱验证码登录</h2>
      <p class="auth-subtitle">本系统不开放公开注册，仅限管理员邀请的邮箱登录使用。</p>

      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        show-icon
        :closable="false"
        class="status-alert"
      />
      <el-alert
        v-if="infoMessage"
        :title="infoMessage"
        type="success"
        show-icon
        :closable="false"
        class="status-alert"
      />

      <el-form label-position="top" @submit.prevent="submitLogin">
        <el-form-item label="登录邮箱">
          <el-input
            v-model="email"
            type="email"
            placeholder="请输入受邀邮箱地址"
            autocomplete="email"
            size="large"
          />
        </el-form-item>

        <el-form-item label="6 位邮箱验证码">
          <div class="code-row">
            <el-input
              v-model="code"
              maxlength="6"
              placeholder="输入 6 位数字验证码"
              autocomplete="one-time-code"
              size="large"
            />
            <el-button
              size="large"
              :loading="sending"
              :disabled="cooldown > 0 || sending"
              @click="sendCode"
            >
              {{ cooldown > 0 ? `${cooldown}s 后重发` : '获取验证码' }}
            </el-button>
          </div>
        </el-form-item>

        <el-button
          type="primary"
          size="large"
          native-type="submit"
          class="auth-submit"
          :loading="submitting"
        >
          验证并登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const email = ref('')
const code = ref('')
const sending = ref(false)
const submitting = ref(false)
const cooldown = ref(0)
const errorMessage = ref('')
const infoMessage = ref('')
let timer: number | null = null

/** 启动 60 秒验证码重发倒计时。 */
function startCooldown(seconds: number): void {
  if (timer !== null) window.clearInterval(timer)
  cooldown.value = seconds
  timer = window.setInterval(() => {
    if (cooldown.value <= 1) {
      cooldown.value = 0
      if (timer !== null) {
        window.clearInterval(timer)
        timer = null
      }
      return
    }
    cooldown.value -= 1
  }, 1000)
}

/** 校验邮箱并请求后端发送 6 位登录验证码。 */
async function sendCode(): Promise<void> {
  errorMessage.value = ''
  infoMessage.value = ''
  const targetEmail = email.value.trim()
  if (!targetEmail) {
    errorMessage.value = '请先输入登录邮箱'
    return
  }
  sending.value = true
  try {
    const result = await auth.requestCode(targetEmail)
    infoMessage.value = `验证码已发送，${Math.floor(result.expiresInSeconds / 60)} 分钟内有效。`
    startCooldown(result.cooldownSeconds)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '验证码发送失败'
  } finally {
    sending.value = false
  }
}

/** 提交邮箱与验证码完成登录并跳转到目标页面。 */
async function submitLogin(): Promise<void> {
  errorMessage.value = ''
  const targetEmail = email.value.trim()
  const targetCode = code.value.trim()
  if (!targetEmail) {
    errorMessage.value = '请输入登录邮箱'
    return
  }
  if (!/^\d{6}$/.test(targetCode)) {
    errorMessage.value = '请输入 6 位数字验证码'
    return
  }
  submitting.value = true
  try {
    await auth.verifyCode(targetEmail, targetCode)
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/')
      ? route.query.redirect
      : '/dashboard'
    await router.replace(redirect)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登录失败，请检查验证码'
  } finally {
    submitting.value = false
  }
}

onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer)
})
</script>
