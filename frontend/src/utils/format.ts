import type { Currency, ValuationStatus } from '../api/types'

const numberFormatters = new Map<number, Intl.NumberFormat>()

/** 获取指定小数位的本地化数字格式器。 */
function getNumberFormatter(digits: number): Intl.NumberFormat {
  const cached = numberFormatters.get(digits)
  if (cached) return cached
  const formatter = new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  numberFormatters.set(digits, formatter)
  return formatter
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

/** 股数按整数展示；仅改变展示精度，不改变服务端账务值。 */
export function formatQuantity(value: string | null | undefined): string {
  return formatDecimal(value, 0)
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
