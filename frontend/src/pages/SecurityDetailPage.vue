<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { Currency, DashboardPosition, PositionRefreshScope } from '../api/types'
import ActivePositionList from '../components/ActivePositionList.vue'
import SecurityDetailPanel from '../components/SecurityDetailPanel.vue'
import { useAccountsStore } from '../stores/accounts'
import { useLedgerStore } from '../stores/ledger'
import { useQuotesStore } from '../stores/quotes'
import { formatMoney, formatSignedPercent, priceDigits, trendClass } from '../utils/format'
import { refreshPosition } from '../utils/recalculate'
import { projectDashboardWithQuotes } from '../utils/valuation'
import { useViewport } from '../utils/viewport'

const route = useRoute()
const router = useRouter()
const accountsStore = useAccountsStore()
const ledger = useLedgerStore()
const quotesStore = useQuotesStore()
const { isMobile } = useViewport()

const routeMarket = computed(() => String(route.params.market ?? '').toUpperCase())
const routeSymbol = computed(() => String(route.params.symbol ?? '').toUpperCase())
const requestedAccountId = computed(() => (typeof route.query.accountId === 'string' ? route.query.accountId : undefined))
const requestedStrategyId = computed(() => (typeof route.query.strategyId === 'string' ? route.query.strategyId : undefined))
const currentAccountId = ref(requestedAccountId.value ?? accountsStore.activeAccountId ?? '')
const currency = computed<Currency>(() => (routeMarket.value === 'US' ? 'USD' : 'CNY'))

/** 当前账户的活动仓位快照（叠加实时行情），供详情页左侧与顶部切换使用。 */
const activePositions = computed<DashboardPosition[]>(() => {
  const snapshot = ledger.dashboard
    ? projectDashboardWithQuotes(ledger.dashboard, quotesStore.quotesMap)
    : null
  if (!snapshot) return []
  if (currentAccountId.value) {
    if (snapshot.accountId !== currentAccountId.value) return []
  } else if (snapshot.currency && snapshot.currency !== currency.value) {
    return []
  }
  return [...snapshot.positions]
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
})

/** 当前正在查看的持仓唯一键，供左侧活动仓位列表高亮。 */
const currentPositionKey = computed<string | null>(() => {
  const matched = activePositions.value.find((pos) => isCurrentPosition(pos))
  return matched ? `${matched.securityId}|${matched.strategyId}` : null
})

/** 判断活动仓位条目是否为当前详情页正在查看的证券。 */
function isCurrentPosition(position: DashboardPosition): boolean {
  return (
    position.market?.toUpperCase() === routeMarket.value
    && position.symbol?.toUpperCase() === routeSymbol.value
  )
}

/** 从详情页活动仓位列表切换到目标个股，并保留当前账户与策略范围。 */
function switchPosition(position: DashboardPosition): void {
  if (!position.market || !position.symbol || isCurrentPosition(position)) return
  void router.push({
    path: `/security/${position.market}/${position.symbol}`,
    query: {
      accountId: currentAccountId.value || ledger.dashboard?.accountId || undefined,
      strategyId: position.strategyId,
    },
  })
}

/** 确保活动仓位快照与当前账户一致，未命中时静默拉取目标账户缓存快照。 */
function ensureActivePositions(targetAccountId = currentAccountId.value): void {
  if (!targetAccountId) return
  if (ledger.dashboard?.accountId === targetAccountId) return
  void ledger.loadDashboard({
    accountId: targetAccountId,
    includeClosed: false,
    mode: 'cache',
  })
}

/** 响应详情面板内账户切换并同步加载活动仓位缓存。 */
function handlePanelAccountChange(nextAccountId: string): void {
  currentAccountId.value = nextAccountId
  ensureActivePositions(nextAccountId)
}

/** 详情面板发生记账或重算后，静默刷新活动仓位缓存快照。 */
function handlePanelMutated(): void {
  const target = currentAccountId.value || ledger.dashboard?.accountId
  if (!target) return
  ledger.invalidateDashboard()
  void ledger.loadDashboard({
    accountId: target,
    includeClosed: false,
    mode: 'auto',
  })
}

/** 响应左侧活动仓位列表中的单行待刷新操作。 */
async function handleListRecalculate(scope: PositionRefreshScope): Promise<void> {
  if (!scope.accountId) return
  await refreshPosition(scope, {
    isCurrent: () => scope.accountId === (currentAccountId.value || ledger.dashboard?.accountId),
    invalidate: () => {
      ledger.invalidateDashboard()
    },
    reload: async () => {
      const result = await ledger.loadDashboard({
        accountId: scope.accountId,
        includeClosed: false,
        mode: 'auto',
      })
      return Boolean(result)
    },
    settled: () => {},
  })
}

watch(
  () => requestedAccountId.value,
  (next) => {
    if (next) {
      currentAccountId.value = next
      ensureActivePositions(next)
    }
  },
  { immediate: true },
)
</script>

<template>
  <template v-if="!isMobile">
    <div :class="['detail-layout', { 'detail-layout--with-aside': activePositions.length > 0 }]">
      <aside v-if="activePositions.length" class="detail-pos-aside" aria-label="活动仓位快速切换">
        <ActivePositionList
          :positions="activePositions"
          :total-open-positions="activePositions.length"
          :currency="currency"
          :account-id="currentAccountId || ledger.dashboard?.accountId || ''"
          :active-key="currentPositionKey"
          @select="switchPosition"
          @recalculate="handleListRecalculate"
        />
      </aside>
      <div class="detail-main">
        <SecurityDetailPanel
          :market="routeMarket"
          :symbol="routeSymbol"
          :account-id="requestedAccountId"
          :strategy-id="requestedStrategyId"
          @account-change="handlePanelAccountChange"
          @mutated="handlePanelMutated"
        />
      </div>
    </div>
  </template>

  <template v-else>
    <nav v-if="activePositions.length" class="m-pos-strip-wrap" aria-label="活动仓位快速切换">
      <div :class="['m-pos-strip', { 'm-pos-strip--two-rows': activePositions.length > 2 }]">
        <button
          v-for="pos in activePositions"
          :key="`${pos.securityId}|${pos.strategyId}`"
          type="button"
          :class="['m-pos-chip', { 'is-active': isCurrentPosition(pos) }]"
          @click="switchPosition(pos)"
        >
          <div class="m-pos-chip-top">
            <strong class="m-pos-chip-name">{{ pos.name || pos.symbol }}</strong>
            <span :class="['m-pos-chip-chg', 'num', trendClass(pos.quote?.changePct)]">{{ formatSignedPercent(pos.quote?.changePct) }}</span>
          </div>
          <div class="m-pos-chip-sub num">
            <span>{{ pos.symbol }}</span>
            <span>{{ pos.quote?.lastPrice ? formatMoney(pos.quote.lastPrice, pos.currency ?? currency, priceDigits(pos, 3)) : '—' }}</span>
          </div>
        </button>
      </div>
    </nav>

    <SecurityDetailPanel
      :market="routeMarket"
      :symbol="routeSymbol"
      :account-id="requestedAccountId"
      :strategy-id="requestedStrategyId"
      @account-change="handlePanelAccountChange"
      @mutated="handlePanelMutated"
    />
  </template>
</template>

<style scoped>
.detail-layout { display: block; }
.detail-layout--with-aside { display: flex; align-items: flex-start; gap: 20px; }
.detail-pos-aside {
  flex: 0 0 480px;
  width: 480px;
  position: sticky;
  top: 16px;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
}
.detail-main { flex: 1; min-width: 0; }
@media (min-width: 769px) and (max-width: 1360px) {
  .detail-pos-aside {
    flex: 0 0 430px;
    width: 430px;
  }
}
@media (min-width: 769px) and (max-width: 1120px) {
  .detail-layout--with-aside {
    flex-direction: column;
  }
  .detail-pos-aside {
    position: static;
    width: 100%;
    flex: none;
    max-height: none;
  }
}
.m-pos-strip-wrap { position: sticky; top: 44px; z-index: 15; margin: -8px -8px 8px; padding: 6px 8px; background: #f2f4f8; border-bottom: 1px solid #e5e9f0; }
.m-pos-strip { display: flex; gap: 6px; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
.m-pos-strip--two-rows { display: grid; grid-auto-flow: column; grid-template-rows: repeat(2, auto); grid-auto-columns: minmax(112px, 138px); gap: 5px 6px; }
.m-pos-strip::-webkit-scrollbar { display: none; }
.m-pos-chip { flex: 0 0 auto; min-width: 112px; max-width: 148px; padding: 4px 7px; border: 1px solid #e2e8f0; border-radius: 7px; background: #fff; text-align: left; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
.m-pos-chip.is-active { border-color: #2563eb; background: #eff6ff; }
.m-pos-chip-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.m-pos-chip-name { font-size: 12px; color: #182230; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.m-pos-chip-chg { font-size: 11px; font-weight: 600; flex-shrink: 0; white-space: nowrap; }
.m-pos-chip-sub { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 10px; color: #7d899b; white-space: nowrap; }
</style>
