import { ref, type Ref } from 'vue'

/** 移动端视口断点查询字符串（与 CSS 768px 断点保持一致）。 */
const MOBILE_MEDIA_QUERY = '(max-width: 768px)'

const sharedIsMobile = ref(false)
let initialized = false

/** 根据当前窗口宽度同步移动端状态。 */
function syncViewportState(): void {
  if (typeof window === 'undefined') return
  if (typeof window.matchMedia === 'function') {
    sharedIsMobile.value = window.matchMedia(MOBILE_MEDIA_QUERY).matches
    return
  }
  sharedIsMobile.value = window.innerWidth <= 768
}

/** 初始化全局单例视口监听器，避免多组件重复绑定事件。 */
function ensureViewportListener(): void {
  if (initialized || typeof window === 'undefined') return
  initialized = true
  syncViewportState()

  if (typeof window.matchMedia === 'function') {
    const mediaQuery = window.matchMedia(MOBILE_MEDIA_QUERY)
    const handleChange = (event: MediaQueryListEvent): void => {
      sharedIsMobile.value = event.matches
    }
    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', handleChange)
    } else if (typeof mediaQuery.addListener === 'function') {
      mediaQuery.addListener(handleChange)
    }
  }
  window.addEventListener('resize', syncViewportState, { passive: true })
}

/** 返回响应式视口状态，用于在同一前端项目内按端切换视图。 */
export function useViewport(): { isMobile: Ref<boolean> } {
  ensureViewportListener()
  return { isMobile: sharedIsMobile }
}
