import { reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { PositionRefreshScope } from '../api/types'

interface RefreshLifecycle {
  isCurrent: () => boolean
  invalidate: () => void
  reload: () => Promise<boolean>
  settled: () => void
}

const refreshingScopes = reactive(new Set<string>())

/** 以账户、个股和策略构成稳定的重算范围标识。 */
function scopeKey(scope: PositionRefreshScope): string {
  return JSON.stringify([scope.accountId, scope.securityId, scope.strategyId ?? ''])
}

/** 确认弹窗和重算请求期间均禁止同一范围重复操作。 */
export function isPositionRefreshing(scope: PositionRefreshScope): boolean {
  return refreshingScopes.has(scopeKey(scope))
}

/** 两处入口共用既有重算接口，区分事务失败与成功后的页面读取失败。 */
export async function refreshPosition(scope: PositionRefreshScope, lifecycle: RefreshLifecycle): Promise<void> {
  const key = scopeKey(scope)
  if (refreshingScopes.has(key)) return
  refreshingScopes.add(key)
  let started = false
  try {
    const label = scope.symbol ? `${scope.market ? `${scope.market}:` : ''}${scope.symbol}` : scope.securityId
    try {
      await ElMessageBox.confirm(`将清理当前账户下 ${label} 的指定策略批次及配对，并按有效交易流水、最近优先等量规则重新构造。原始交易记录保留，批次止盈将恢复；价格或数量已更改时，请核对显式止盈目标。是否继续？`, '重新计算持仓', { confirmButtonText: '确认重新计算', cancelButtonText: '取消', type: 'warning' })
    } catch { return }
    if (!lifecycle.isCurrent()) return
    started = true
    lifecycle.invalidate()
    let result
    try {
      result = await api.recalculate(scope.securityId, scope.accountId, scope.strategyId)
    } catch (cause) {
      if (lifecycle.isCurrent()) ElMessage.error(`重新计算失败：${cause instanceof Error ? cause.message : '原数据已保留'}。已保存的流水及待刷新状态保留，可更正后重试。`)
      return
    }
    if (!lifecycle.isCurrent()) return
    lifecycle.invalidate()
    try {
      const loaded = await lifecycle.reload()
      if (!lifecycle.isCurrent()) return
      if (!loaded) throw new Error('请重新读取页面数据')
      ElMessage.success(`已重建 ${result.rebuiltLots} 个批次、${result.rebuiltMatches} 条配对`)
    } catch (cause) {
      if (lifecycle.isCurrent()) ElMessage.warning(`重算已完成，页面数据读取失败：${cause instanceof Error ? cause.message : '请重试读取页面'}。`)
    }
  } finally {
    refreshingScopes.delete(key)
    if (started) lifecycle.settled()
  }
}
