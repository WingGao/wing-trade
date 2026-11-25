/** 前端统一北京时间（Asia/Shanghai / +08:00）与强时区参数处理工具。 */

export const BEIJING_TIMEZONE = 'Asia/Shanghai'
export const BEIJING_OFFSET = '+08:00'

const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000
const TIMEZONE_SUFFIX_RE = /(?:Z|[+-]\d{2}:\d{2})$/i
const PICKER_VALUE_RE = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2})(?:\.(\d+))?)?$/
const FRACTION_RE = /\.(\d+)(?=(?:Z|[+-]\d{2}:\d{2})?$)/i

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: BEIJING_TIMEZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const axisShortDateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: BEIJING_TIMEZONE,
  month: '2-digit',
  day: '2-digit',
})

const axisFullDateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: BEIJING_TIMEZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const axisTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: BEIJING_TIMEZONE,
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/** 将数字补齐为指定长度的零填充字符串。 */
function padNumber(value: number, length = 2): string {
  return String(value).padStart(length, '0')
}

/** 判断时间文本是否显式携带时区后缀（Z 或 ±HH:MM）。 */
export function hasTimezoneOffset(value: string): boolean {
  return TIMEZONE_SUFFIX_RE.test(value.trim())
}

/** 解析服务端时间字符串；若兼容历史无时区文本则按 UTC 补齐后解析。 */
export function parseServerDate(value: string): Date {
  const trimmed = value.trim()
  if (!trimmed) return new Date(Number.NaN)
  const normalized = hasTimezoneOffset(trimmed) ? trimmed : `${trimmed}Z`
  return new Date(normalized)
}

/** 将 Date 对象按固定 +08:00 偏移格式化为北京时间的年月日时分秒组件。 */
function toBeijingParts(date: Date): {
  year: string
  month: string
  day: string
  hour: string
  minute: string
  second: string
} {
  const shifted = new Date(date.getTime() + BEIJING_OFFSET_MS)
  return {
    year: padNumber(shifted.getUTCFullYear(), 4),
    month: padNumber(shifted.getUTCMonth() + 1, 2),
    day: padNumber(shifted.getUTCDate(), 2),
    hour: padNumber(shifted.getUTCHours(), 2),
    minute: padNumber(shifted.getUTCMinutes(), 2),
    second: padNumber(shifted.getUTCSeconds(), 2),
  }
}

/** 返回当前北京时间对应的日期选择器值（YYYY-MM-DDTHH:mm），不受浏览器本地时区影响。 */
export function nowBeijingPickerValue(now: Date = new Date()): string {
  const parts = toBeijingParts(now)
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}

/** 将服务端或带时区时间无损转换为带 +08:00 的北京时间 ISO 字符串，保留原小数秒精度。 */
export function toBeijingIsoString(value: string): string {
  const trimmed = value.trim()
  const date = parseServerDate(trimmed)
  if (Number.isNaN(date.getTime())) return trimmed
  const parts = toBeijingParts(date)
  const fractionMatch = FRACTION_RE.exec(trimmed)
  const fraction = fractionMatch ? `.${fractionMatch[1]}` : ''
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}${fraction}${BEIJING_OFFSET}`
}

/** 统一转换前端请求时间参数为必带时区的北京时间 ISO 字符串（默认 +08:00）。 */
export function toAwareBeijingIso(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) {
    throw new Error('请输入有效的成交时间')
  }
  if (hasTimezoneOffset(trimmed)) {
    const parsed = new Date(trimmed)
    if (Number.isNaN(parsed.getTime())) {
      throw new Error('请输入带时区的有效成交时间')
    }
    return toBeijingIsoString(trimmed)
  }
  const pickerMatch = PICKER_VALUE_RE.exec(trimmed)
  if (pickerMatch) {
    const [, datePart, hmPart, secPart = '00', fracPart] = pickerMatch
    const fraction = fracPart ? `.${fracPart}` : ''
    const iso = `${datePart}T${hmPart}:${secPart}${fraction}${BEIJING_OFFSET}`
    if (Number.isNaN(Date.parse(iso))) {
      throw new Error('请输入有效的成交时间')
    }
    return iso
  }
  throw new Error('成交时间须包含时区，例如 +08:00 或 Z')
}

/** 校验手输时间参数必须显式携带时区且为合法时间，通过后返回去首尾空白的原文本。 */
export function requireTimezoneIso(value: string): string {
  const trimmed = value.trim()
  if (!trimmed || Number.isNaN(Date.parse(trimmed))) {
    throw new Error('请输入带时区的有效成交时间')
  }
  if (!hasTimezoneOffset(trimmed)) {
    throw new Error('成交时间须包含时区，例如 +08:00 或 Z')
  }
  return trimmed
}

/** 将 ISO 时间统一格式化为北京时间可读字符串，无效或缺失时显示破折号。 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = parseServerDate(value)
  return Number.isNaN(date.getTime()) ? '—' : dateTimeFormatter.format(date)
}

/** 按图表曲线类型将时间戳格式化为北京时间标签，不受浏览器所在时区影响。 */
export function formatBeijingAxisTime(
  stamp: number,
  mode: 'date-short' | 'date-full' | 'time',
): string {
  const date = new Date(stamp)
  if (Number.isNaN(date.getTime())) return '—'
  if (mode === 'date-short') return axisShortDateFormatter.format(date)
  if (mode === 'date-full') return axisFullDateFormatter.format(date)
  return axisTimeFormatter.format(date)
}
