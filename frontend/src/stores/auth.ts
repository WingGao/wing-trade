import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import type { AuthUser, RequestCodeRead } from '../api/types'

/** 管理当前登录用户、初始化探测及登录/退出状态。 */
export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)
  const initialized = ref(false)
  const loading = ref(false)

  const isAuthenticated = computed(() => user.value !== null)
  const isAdmin = computed(() => user.value?.role === 'ADMIN')

  /** 应用启动或首次路由守卫时探测服务端会话状态。 */
  async function init(): Promise<void> {
    if (initialized.value) return
    loading.value = true
    try {
      const response = await api.me()
      user.value = response.user
    } catch {
      user.value = null
    } finally {
      initialized.value = true
      loading.value = false
    }
  }

  /** 请求发送邮箱登录验证码。 */
  async function requestCode(email: string): Promise<RequestCodeRead> {
    return api.requestLoginCode(email)
  }

  /** 提交邮箱与验证码完成登录。 */
  async function verifyCode(email: string, code: string): Promise<AuthUser> {
    const response = await api.verifyLoginCode(email, code)
    user.value = response.user
    initialized.value = true
    return response.user
  }

  /** 主动退出登录并清理本地认证状态。 */
  async function logout(): Promise<void> {
    try {
      await api.logout()
    } finally {
      user.value = null
      initialized.value = true
    }
  }

  /** 在收到 401 时立即清空前端用户态。 */
  function clearAuth(): void {
    user.value = null
    initialized.value = true
  }

  return {
    user,
    initialized,
    loading,
    isAuthenticated,
    isAdmin,
    init,
    requestCode,
    verifyCode,
    logout,
    clearAuth,
  }
})
