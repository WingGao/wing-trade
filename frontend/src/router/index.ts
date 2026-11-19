import { createRouter, createWebHistory } from 'vue-router'
import AdminUsersPage from '../pages/AdminUsersPage.vue'
import AnalyticsPage from '../pages/AnalyticsPage.vue'
import DashboardPage from '../pages/DashboardPage.vue'
import LoginPage from '../pages/LoginPage.vue'
import SecurityDetailPage from '../pages/SecurityDetailPage.vue'
import TradesPage from '../pages/TradesPage.vue'
import { useAuthStore } from '../stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/login', component: LoginPage, meta: { public: true, title: '登录' } },
    { path: '/dashboard', component: DashboardPage, meta: { title: '总览' } },
    { path: '/security/:market/:symbol', component: SecurityDetailPage, meta: { title: '持仓详情' } },
    { path: '/trades', component: TradesPage, meta: { title: '交易流水' } },
    { path: '/analytics', component: AnalyticsPage, meta: { title: '复盘分析' } },
    { path: '/admin/users', component: AdminUsersPage, meta: { requiresAdmin: true, title: '用户管理' } },
  ],
})

/** 全局路由守卫：拦截未登录访问与非管理员越权访问。 */
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.initialized) {
    await auth.init()
  }
  if (to.meta.public) {
    if (auth.isAuthenticated && to.path === '/login') {
      return '/dashboard'
    }
    return true
  }
  if (!auth.isAuthenticated) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAdmin && !auth.isAdmin) {
    return '/dashboard'
  }
  return true
})
