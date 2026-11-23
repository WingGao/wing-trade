import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '../api/client'
import type { Account, Currency } from '../api/types'
import { useAuthStore } from './auth'

const STORAGE_PREFIX = 'wing_trade_active_account:'

export const useAccountsStore = defineStore('accounts', () => {
  const auth = useAuthStore()
  const accounts = ref<Account[]>([])
  const activeAccountId = ref('')
  const loading = ref(false)
  const loadedUserId = ref('')

  const activeAccount = computed<Account | null>(
    () => accounts.value.find((item) => item.id === activeAccountId.value) ?? null,
  )

  /** 返回按当前登录用户隔离的本地存储键名。 */
  function storageKey(): string {
    return `${STORAGE_PREFIX}${auth.user?.id ?? 'anonymous'}`
  }

  /** 安全读取本地持久化的上次选中账户 ID。 */
  function readStoredAccountId(): string {
    try {
      return window.localStorage.getItem(storageKey()) ?? ''
    } catch {
      return ''
    }
  }

  /** 将当前选中的账户 ID 持久化到浏览器本地存储。 */
  function persistAccountId(accountId: string): void {
    try {
      if (accountId) window.localStorage.setItem(storageKey(), accountId)
      else window.localStorage.removeItem(storageKey())
    } catch {
      // 隐私模式或存储受限时忽略持久化失败。
    }
  }

  /** 加载当前用户的全部资金账户，并确保 activeAccountId 指向有效账户。 */
  async function loadAccounts(force = false): Promise<Account[]> {
    const userId = auth.user?.id ?? ''
    if (!force && loadedUserId.value === userId && accounts.value.length > 0) {
      return accounts.value
    }
    loading.value = true
    try {
      const result = await api.accounts()
      accounts.value = result
      loadedUserId.value = userId
      const currentValid = result.some((item) => item.id === activeAccountId.value)
      if (!currentValid) {
        const stored = readStoredAccountId()
        const storedValid = result.some((item) => item.id === stored)
        const nextId = storedValid
          ? stored
          : (result.find((item) => item.currency === 'CNY')?.id ?? result[0]?.id ?? '')
        activeAccountId.value = nextId
        persistAccountId(nextId)
      }
      return result
    } finally {
      loading.value = false
    }
  }

  /** 切换全局当前活跃账户并持久化选择。 */
  function setActiveAccount(accountId: string): void {
    if (!accountId || !accounts.value.some((item) => item.id === accountId)) return
    activeAccountId.value = accountId
    persistAccountId(accountId)
  }

  /** 创建新的资金账户（CNY 或 USD），并将其设为当前活跃账户。 */
  async function createAccount(payload: {
    name: string
    currency: Currency
    broker?: string | null
  }): Promise<Account> {
    const created = await api.createAccount({
      name: payload.name.trim(),
      currency: payload.currency,
      broker: payload.broker?.trim() || null,
    })
    accounts.value = [...accounts.value, created]
    activeAccountId.value = created.id
    persistAccountId(created.id)
    return created
  }

  /** 退出登录或切换用户时清空内存中的账户缓存。 */
  function reset(): void {
    accounts.value = []
    activeAccountId.value = ''
    loadedUserId.value = ''
  }

  return {
    accounts,
    activeAccountId,
    activeAccount,
    loading,
    loadAccounts,
    setActiveAccount,
    createAccount,
    reset,
  }
})
