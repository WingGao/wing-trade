import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const SHOW_WATCHLIST_STORAGE_KEY = 'wing-trade:showWatchlist'
const MERGE_LOTS_BY_PRICE_STORAGE_KEY = 'wing-trade:mergeLotsByPrice'

/** 从本地存储读取布尔偏好，缺失或不可用时回退默认值。 */
function readStoredBoolean(key: string, fallback: boolean): boolean {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return fallback
  }
  try {
    const raw = window.localStorage.getItem(key)
    if (raw === 'true') return true
    if (raw === 'false') return false
    return fallback
  } catch {
    return fallback
  }
}

/** 将布尔偏好写入本地存储，忽略隐私模式下的存储异常。 */
function writeStoredBoolean(key: string, value: boolean): void {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(key, value ? 'true' : 'false')
  } catch {
    // 忽略本地存储写入异常
  }
}

export const usePreferencesStore = defineStore('preferences', () => {
  const includeClosed = ref(false)
  const showWatchlist = ref(readStoredBoolean(SHOW_WATCHLIST_STORAGE_KEY, true))
  const mergeLotsByPrice = ref(readStoredBoolean(MERGE_LOTS_BY_PRICE_STORAGE_KEY, true))
  const redUp = ref(true)

  /** 切换是否显示历史已结清仓位。 */
  const toggleClosed = () => {
    includeClosed.value = !includeClosed.value
  }

  /** 切换是否在首页列表混入已关注的清仓标的。 */
  const toggleWatchlist = () => {
    showWatchlist.value = !showWatchlist.value
  }

  /** 切换持仓批次是否按买入价格合并展示。 */
  const toggleMergeLotsByPrice = () => {
    mergeLotsByPrice.value = !mergeLotsByPrice.value
  }

  watch(showWatchlist, (next) => {
    writeStoredBoolean(SHOW_WATCHLIST_STORAGE_KEY, next)
  })

  watch(mergeLotsByPrice, (next) => {
    writeStoredBoolean(MERGE_LOTS_BY_PRICE_STORAGE_KEY, next)
  })

  return {
    includeClosed,
    showWatchlist,
    mergeLotsByPrice,
    redUp,
    toggleClosed,
    toggleWatchlist,
    toggleMergeLotsByPrice,
  }
})
