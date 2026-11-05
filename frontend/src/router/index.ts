import { createRouter, createWebHistory } from 'vue-router'
import DashboardPage from '../pages/DashboardPage.vue'
import SecurityDetailPage from '../pages/SecurityDetailPage.vue'
import TradesPage from '../pages/TradesPage.vue'
import AnalyticsPage from '../pages/AnalyticsPage.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', component: DashboardPage },
    { path: '/security/:market/:symbol', component: SecurityDetailPage },
    { path: '/trades', component: TradesPage },
    { path: '/analytics', component: AnalyticsPage },
  ],
})
