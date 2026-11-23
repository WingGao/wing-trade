<template>
  <router-view v-if="isPublicRoute" />
  <div v-else-if="isMobile" class="m-shell">
    <header class="m-topbar">
      <div class="m-topbar-left">
        <span class="m-brand-mark">翼</span>
        <h1 class="m-topbar-title">{{ pageTitle }}</h1>
      </div>
      <div v-if="auth.user" class="m-topbar-user">
        <el-select
          v-if="accountsStore.accounts.length"
          :model-value="accountsStore.activeAccountId"
          size="small"
          class="m-account-switcher"
          placeholder="账户"
          @update:model-value="handleAccountChange"
        >
          <el-option
            v-for="account in accountsStore.accounts"
            :key="account.id"
            :label="`${account.name} (${account.currency})`"
            :value="account.id"
          />
        </el-select>
        <button type="button" class="m-add-account-btn" title="新建账户" @click="openCreateAccount">+</button>
        <button type="button" class="m-logout-btn" :disabled="loggingOut" @click="handleLogout">
          {{ loggingOut ? '退出中' : '退出' }}
        </button>
      </div>
    </header>
    <main class="m-main"><router-view /></main>
    <nav class="m-tabbar" aria-label="移动端主导航">
      <router-link to="/dashboard" class="m-tab-item" :class="{ 'is-active': isNavActive('/dashboard') }">
        <span>总览</span>
        <span class="m-tab-sub">持仓</span>
      </router-link>
      <router-link to="/trades" class="m-tab-item" :class="{ 'is-active': isNavActive('/trades') }">
        <span>流水</span>
        <span class="m-tab-sub">记录</span>
      </router-link>
      <router-link to="/analytics" class="m-tab-item" :class="{ 'is-active': isNavActive('/analytics') }">
        <span>复盘</span>
        <span class="m-tab-sub">周期</span>
      </router-link>
    </nav>
  </div>
  <el-container v-else class="shell">
    <el-aside width="220px" class="sidebar">
      <div class="brand">
        <span class="brand-mark">翼</span>
        <div><strong>翼·交易账本</strong><small>Portfolio Journal</small></div>
      </div>
      <el-menu :default-active="route.path" router class="nav" background-color="transparent" text-color="#a7b1c2" active-text-color="#fff">
        <el-menu-item index="/dashboard">总览 Dashboard</el-menu-item>
        <el-menu-item index="/trades">交易流水 Trades</el-menu-item>
        <el-menu-item index="/analytics">复盘分析 Analytics</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/admin/users">用户管理 Users</el-menu-item>
      </el-menu>
      <div class="sidebar-foot">账本优先 · 行情仅作参考</div>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <div>
          <span class="eyebrow">自托管投资工作台</span>
          <h1>{{ route.path === '/dashboard' ? '总览' : '投资账本' }}</h1>
        </div>
        <div class="topbar-user" v-if="auth.user">
          <div class="topbar-account">
            <el-select
              v-if="accountsStore.accounts.length"
              :model-value="accountsStore.activeAccountId"
              size="small"
              style="width: 196px"
              placeholder="选择资金账户"
              @update:model-value="handleAccountChange"
            >
              <el-option
                v-for="account in accountsStore.accounts"
                :key="account.id"
                :label="`${account.name} (${account.currency})`"
                :value="account.id"
              />
            </el-select>
            <el-button size="small" plain @click="openCreateAccount">+ 新建账户</el-button>
          </div>
          <div class="user-meta">
            <span class="user-email">{{ auth.user.displayName || auth.user.email }}</span>
            <el-tag :type="auth.isAdmin ? 'danger' : 'success'" size="small" effect="dark">
              {{ auth.isAdmin ? '管理员' : '普通用户' }}
            </el-tag>
          </div>
          <el-button size="small" plain :loading="loggingOut" @click="handleLogout">退出登录</el-button>
        </div>
      </el-header>
      <el-main class="main"><router-view /></el-main>
    </el-container>
  </el-container>
  <CreateAccountDialog v-if="auth.user" v-model="createAccountVisible" :default-currency="createDefaultCurrency" />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { setUnauthorizedHandler } from './api/client'
import type { Currency, DashboardPosition } from './api/types'
import CreateAccountDialog from './components/CreateAccountDialog.vue'
import { useAccountsStore } from './stores/accounts'
import { useAuthStore } from './stores/auth'
import { useLedgerStore } from './stores/ledger'
import { useViewport } from './utils/viewport'

const auth = useAuthStore()
const accountsStore = useAccountsStore()
const ledgerStore = useLedgerStore()
const route = useRoute()
const router = useRouter()
const { isMobile } = useViewport()
const loggingOut = ref(false)
const createAccountVisible = ref(false)
const createDefaultCurrency = ref<Currency>('USD')
let accountSwitchSequence = 0

const isPublicRoute = computed(() => Boolean(route.meta.public))
/** 计算当前页面标题，供移动端极简顶栏展示。 */
const pageTitle = computed(() => (route.path === '/dashboard' ? '总览' : String(route.meta.title ?? '投资账本')))

/** 判断底部导航项是否处于当前激活路由。 */
function isNavActive(path: string): boolean {
  if (path === '/dashboard') return route.path === '/dashboard' || route.path.startsWith('/security/')
  return route.path === path
}

/** 按当前涨幅降序挑出目标账户的第一个有效活动持仓，与详情页活动仓位列表排序一致。 */
function pickFirstActivePosition(positions: DashboardPosition[]): DashboardPosition | null {
  const sorted = [...positions]
    .filter((position) => Boolean(position.market && position.symbol))
    .sort((left, right) => {
      const leftVal = Number(left.quote?.changePct)
      const rightVal = Number(right.quote?.changePct)
      const leftValid = Number.isFinite(leftVal)
      const rightValid = Number.isFinite(rightVal)
      if (!leftValid && !rightValid) return 0
      if (!leftValid) return 1
      if (!rightValid) return -1
      return rightVal - leftVal
    })
  return sorted[0] ?? null
}

/** 切换全局资金账户；若当前停留在个股详情页，则跳转到目标账户的第一个持仓详情（无持仓时回到总览）。 */
async function handleAccountChange(accountId: string): Promise<void> {
  accountsStore.setActiveAccount(accountId)
  if (!route.path.startsWith('/security/')) return
  const requestId = ++accountSwitchSequence
  const snapshot = await ledgerStore.loadDashboard({
    accountId,
    includeClosed: false,
    mode: 'cache',
  })
  if (requestId !== accountSwitchSequence || !route.path.startsWith('/security/')) return
  const first = snapshot ? pickFirstActivePosition(snapshot.positions) : null
  if (first?.market && first?.symbol) {
    await router.push({
      path: `/security/${first.market}/${first.symbol}`,
      query: {
        accountId,
        strategyId: first.strategyId,
      },
    })
    return
  }
  await router.push('/dashboard')
}

/** 打开新建资金账户弹窗，若当前尚无美元账户则默认推荐 USD。 */
function openCreateAccount(): void {
  const hasUsd = accountsStore.accounts.some((item) => item.currency === 'USD')
  createDefaultCurrency.value = hasUsd ? 'CNY' : 'USD'
  createAccountVisible.value = true
}

/** 处理主动退出登录并返回登录页。 */
async function handleLogout(): Promise<void> {
  loggingOut.value = true
  try {
    await auth.logout()
    accountsStore.reset()
    await router.replace('/login')
  } finally {
    loggingOut.value = false
  }
}

watch(
  () => auth.user?.id,
  (userId) => {
    if (userId) void accountsStore.loadAccounts()
    else accountsStore.reset()
  },
  { immediate: true },
)

onMounted(() => {
  setUnauthorizedHandler(() => {
    auth.clearAuth()
    accountsStore.reset()
    if (router.currentRoute.value.path !== '/login') {
      void router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
    }
  })
})

onBeforeUnmount(() => {
  setUnauthorizedHandler(null)
})
</script>

<style scoped>
.topbar-account { display: flex; align-items: center; gap: 8px; }
.m-account-switcher { width: 126px; }
.m-add-account-btn { border: 1px solid #d0d7e2; background: #fff; color: #182230; border-radius: 4px; width: 24px; height: 24px; line-height: 20px; font-size: 14px; font-weight: 700; cursor: pointer; padding: 0; }
</style>
