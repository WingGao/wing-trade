import type { Currency, MarketSessionPhase, ValuationStatus } from '../api/types'

const numberFormatters = new Map<string, Intl.NumberFormat>()

/** 获取指定小数位区间的本地化数字格式器。 */
function getNumberFormatter(minDigits: number, maxDigits = minDigits): Intl.NumberFormat {
  const key = `${minDigits}:${maxDigits}`
  const cached = numberFormatters.get(key)
  if (cached) return cached
  const formatter = new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: minDigits,
    maximumFractionDigits: maxDigits,
  })
  numberFormatters.set(key, formatter)
  return formatter
}

/** 判断标的或交易对象是否属于场外开放式基金。 */
export function isFundSecurity(
  target?: { market?: string | null; securityType?: string | null; symbol?: string | null } | null,
): boolean {
  if (!target) return false
  if (target.market?.toUpperCase() === 'OF') return true
  if (target.securityType?.toUpperCase() === 'FUND') return true
  return Boolean(target.symbol?.toUpperCase().startsWith('OF:'))
}

/** 判断标的或交易对象是否属于美股市场。 */
export function isUSSecurity(
  target?: { market?: string | null; currency?: string | null; symbol?: string | null } | null,
): boolean {
  if (!target) return false
  if (target.market?.toUpperCase() === 'US') return true
  if (target.currency?.toUpperCase() === 'USD') return true
  return Boolean(target.symbol?.toUpperCase().startsWith('US:'))
}

/** 返回交易时段的简短中文标签，便于区分盘前、盘中与盘后。 */
export function marketSessionLabel(phase: MarketSessionPhase | null | undefined): string {
  switch (phase) {
    case 'pre': return '盘前'
    case 'post': return '盘后'
    case 'regular': return '盘中'
    case 'closed': return '休市'
    default: return ''
  }
}

/** 返回标的对应的数量展示单位（基金为“份”，股票为“股”）。 */
export function quantityUnit(
  target?: { market?: string | null; securityType?: string | null; symbol?: string | null } | boolean | null,
): string {
  const fund = typeof target === 'boolean' ? target : isFundSecurity(target)
  return fund ? '份' : '股'
}

/** 返回标的对应的价格/净值展示小数位（场外基金净值固定 4 位）。 */
export function priceDigits(
  target?: { market?: string | null; securityType?: string | null; symbol?: string | null } | boolean | null,
  defaultDigits = 3,
): number {
  const fund = typeof target === 'boolean' ? target : isFundSecurity(target)
  return fund ? 4 : defaultDigits
}

/** 将服务端十进制字符串格式化为有限小数，无法解析时显示破折号。 */
export function formatDecimal(value: string | null | undefined, digits = 2): string {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? getNumberFormatter(digits).format(parsed === 0 ? 0 : parsed) : '—'
}

/** 将金额按账户币种格式化，缺失值不会被误显示为零。 */
export function formatMoney(value: string | null | undefined, currency: Currency | null | undefined, digits = 2): string {
  const formatted = formatDecimal(value, digits)
  if (formatted === '—') return formatted
  const symbol = currency === 'USD' ? '$' : currency === 'CNY' ? '¥' : ''
  return `${symbol} ${formatted}`.trim()
}

/** 基金份额按 2 至 4 位小数展示，美股或含小数股数保留最多 4 位有效小数，A 股整数股按整数展示。 */
export function formatQuantity(
  value: string | null | undefined,
  target: { market?: string | null; securityType?: string | null; symbol?: string | null; currency?: string | null } | boolean | null = false,
): string {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  const normalized = parsed === 0 ? 0 : parsed
  const isFund = typeof target === 'boolean' ? target : isFundSecurity(target)
  if (isFund) return getNumberFormatter(2, 4).format(normalized)
  const isUS = typeof target === 'object' && target !== null && isUSSecurity(target)
  const hasFraction = Math.abs(normalized - Math.trunc(normalized)) > 1e-9
  if (isUS || hasFraction) return getNumberFormatter(0, 4).format(normalized)
  return getNumberFormatter(0).format(normalized)
}

/** 以字符串移动十进制小数点，避免止盈比例转换混入浮点误差。 */
function shiftDecimal(value: string, places: number): string {
  const match = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(value.trim())
  if (!match || !(match[2] || match[3])) throw new Error('请输入有效的收益率')
  const sign = match[1] === '-' ? '-' : ''
  const whole = match[2] || '0'
  const fraction = match[3] || ''
  const digits = whole + fraction
  const boundary = whole.length + places
  const shifted = boundary <= 0 ? `0.${'0'.repeat(-boundary)}${digits}` : boundary >= digits.length ? `${digits}${'0'.repeat(boundary - digits.length)}` : `${digits.slice(0, boundary)}.${digits.slice(boundary)}`
  const [integer = '0', decimal = ''] = shifted.split('.')
  const normalizedInteger = integer.replace(/^0+(?=\d)/, '')
  const normalizedDecimal = decimal.replace(/0+$/, '')
  return `${sign}${normalizedInteger}${normalizedDecimal ? `.${normalizedDecimal}` : ''}`
}

/** 将弹窗输入的百分数精确转换为服务端小数收益率，例如 10→0.1。 */
export function percentInputToRate(value: string): string {
  return shiftDecimal(value, -2)
}

/** 将服务端小数收益率精确转换为表单百分数，例如 0.1→10。 */
export function rateToPercentInput(value: string): string {
  return shiftDecimal(value, 2)
}

/** 展示止盈的小数率，区别于服务端已乘百的浮盈收益率。 */
export function formatTargetPercent(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  try { return formatPercent(rateToPercentInput(value)) } catch { return '—' }
}

/** 将收益率服务端字符串格式化为百分数，缺失值显示破折号。 */
export function formatPercent(value: string | null | undefined): string {
  const formatted = formatDecimal(value)
  return formatted === '—' ? formatted : `${formatted}%`
}

/** 将涨跌幅格式化并明确保留零值及正数符号。 */
export function formatSignedPercent(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  const sign = parsed > 0 ? '+' : ''
  return `${sign}${formatDecimal(value)}%`
}

/** 根据服务端十进制值返回涨跌样式，零值和缺失值保持中性。 */
export function trendClass(value: string | null | undefined): 'up' | 'down' | 'neutral' {
  if (value === null || value === undefined || value === '') return 'neutral'
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed === 0) return 'neutral'
  return parsed > 0 ? 'up' : 'down'
}

/** 将 ISO 时间转换为本地可读时间，无效或缺失时显示破折号。 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

/** 返回估值状态的简短中文标签。 */
export function valuationStatusLabel(status: ValuationStatus | null | undefined): string {
  switch (status) {
    case 'fresh': return '行情完整'
    case 'stale': return '行情已过期'
    case 'partial': return '行情覆盖不完整'
    case 'unavailable': return '行情不可用'
    case 'empty': return '暂无持仓'
    default: return '尚未加载'
  }
}
