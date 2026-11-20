<template>
  <router-view v-if="isPublicRoute" />
  <div v-else-if="isMobile" class="m-shell">
    <header class="m-topbar">
      <div class="m-topbar-left">
        <span class="m-brand-mark">翼</span>
        <h1 class="m-topbar-title">{{ pageTitle }}</h1>
      </div>
      <div v-if="auth.user" class="m-topbar-user">
        <span class="m-user-name">{{ userShortName }}</span>
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
        <div><strong>A 股交易账本</strong><small>Portfolio Journal</small></div>
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
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { setUnauthorizedHandler } from './api/client'
import { useAuthStore } from './stores/auth'
import { useViewport } from './utils/viewport'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const { isMobile } = useViewport()
const loggingOut = ref(false)

const isPublicRoute = computed(() => Boolean(route.meta.public))
/** 计算当前页面标题，供移动端极简顶栏展示。 */
const pageTitle = computed(() => (route.path === '/dashboard' ? '总览' : String(route.meta.title ?? '投资账本')))
/** 计算用户紧凑显示名，避免移动端顶栏溢出。 */
const userShortName = computed(() => {
  const raw = auth.user?.displayName || auth.user?.email || ''
  return raw.includes('@') ? raw.split('@')[0] : raw
})

/** 判断底部导航项是否处于当前激活路由。 */
function isNavActive(path: string): boolean {
  if (path === '/dashboard') return route.path === '/dashboard' || route.path.startsWith('/security/')
  return route.path === path
}

/** 处理主动退出登录并返回登录页。 */
async function handleLogout(): Promise<void> {
  loggingOut.value = true
  try {
    await auth.logout()
    await router.replace('/login')
  } finally {
    loggingOut.value = false
  }
}

onMounted(() => {
  setUnauthorizedHandler(() => {
    auth.clearAuth()
    if (router.currentRoute.value.path !== '/login') {
      void router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
    }
  })
})

onBeforeUnmount(() => {
  setUnauthorizedHandler(null)
})
</script>
