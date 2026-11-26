import type { StrategyRowInput, StrategyRowStatus } from '../api/types'

export interface StrategyParseError {
  line: number
  message: string
}

export interface StrategyParseResult {
  rows: StrategyRowInput[]
  errors: StrategyParseError[]
  warnings: string[]
}

/** 完全固定的静态 AI 提示词：引导外部 AI 输出与解析器兼容的 5 列标准 Markdown 策略表。 */
export const STRATEGY_TABLE_AI_PROMPT = `请根据我们上面的分析，为当前标的制定一份分批/网格买卖策略表，并严格按照以下 Markdown 表格格式输出（不要拆分多个表格）：

| 买入价 | 买入数量 | 卖出价 | 卖出数量 | 备注 |
| --- | --- | --- | --- | --- |
| 10.50 | 1000 | 11.80 | 1000 | 第一档：支撑位低吸与压力位高抛 |
| 9.80 | 1500 | 11.20 | 1500 | 第二档：回调加仓 |
| - | - | 12.60 | 500 | 高位分批止盈（仅卖出留空买入侧） |

格式要求：
1. 必须包含表头：买入价、买入数量、卖出价、卖出数量、备注。
2. 买入价、买入数量、卖出价、卖出数量请只填纯正数（不要写区间如 10-11，不要带货币符号或单位文字）。
3. 若某档只有买入计划或只有卖出计划，另一侧请填 "-" 留空。
4. 备注列简明说明触发逻辑、支撑/压力位或仓位意图，避免换行。`

const EMPTY_PLACEHOLDERS = new Set(['', '-', '--', '—', '——', '无', '空', 'n/a', 'na', 'none', 'null', '/'])

type ColumnKey = 'tier' | 'buyPrice' | 'buyQty' | 'sellPrice' | 'sellQty' | 'note' | 'status'

/** 将 Markdown 表格的一行按未转义的竖线拆分为单元格文本数组。 */
function splitMarkdownRow(line: string): string[] {
  let trimmed = line.trim()
  if (trimmed.startsWith('|')) trimmed = trimmed.slice(1)
  if (trimmed.endsWith('|')) trimmed = trimmed.slice(0, -1)
  return trimmed.split('|').map((cell) => cell.trim())
}

/** 判断某行是否为 Markdown 表格的对齐分隔行（例如 | --- | ---: |）。 */
function isSeparatorRow(line: string): boolean {
  const trimmed = line.trim()
  if (!trimmed.includes('|') || !trimmed.includes('-')) return false
  const cells = splitMarkdownRow(trimmed)
  if (cells.length < 2) return false
  return cells.every((cell) => /^:?-{2,}:?$/.test(cell))
}

/** 从包含说明文字或代码块的原始文本中定位首个有效 Markdown 表格区块。 */
function extractFirstTableBlock(rawText: string): {
  headerCells: string[]
  dataLines: Array<{ lineNo: number; cells: string[] }>
} | null {
  const lines = rawText.replace(/\r\n?/g, '\n').split('\n')
  for (let i = 0; i < lines.length - 1; i += 1) {
    const headerLine = lines[i] ?? ''
    const sepLine = lines[i + 1] ?? ''
    if (!headerLine.includes('|') || !isSeparatorRow(sepLine)) continue
    const headerCells = splitMarkdownRow(headerLine)
    if (headerCells.length < 2) continue

    const dataLines: Array<{ lineNo: number; cells: string[] }> = []
    for (let j = i + 2; j < lines.length; j += 1) {
      const current = (lines[j] ?? '').trim()
      if (!current || !current.includes('|') || current.startsWith('```')) break
      if (isSeparatorRow(current)) continue
      dataLines.push({ lineNo: j + 1, cells: splitMarkdownRow(current) })
    }
    return { headerCells, dataLines }
  }
  return null
}

/** 按 RFC 4180 规则将单行 CSV/TSV 文本拆分为单元格数组，支持双引号包裹字段与转义引号。 */
function splitDelimitedRow(line: string, delimiter: ',' | '\t'): string[] {
  const cells: string[] = []
  let current = ''
  let inQuotes = false
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i]
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          current += '"'
          i += 1
        } else {
          inQuotes = false
        }
      } else {
        current += ch
      }
    } else if (ch === '"') {
      inQuotes = true
    } else if (ch === delimiter) {
      cells.push(current.trim())
      current = ''
    } else {
      current += ch
    }
  }
  cells.push(current.trim())
  return cells
}

/** 从原始文本中提取首个有效的 CSV 或 TSV 表格区块（兼容代码块与前后说明文字）。 */
function extractFirstCsvBlock(rawText: string): {
  headerCells: string[]
  dataLines: Array<{ lineNo: number; cells: string[] }>
  headerIsDataRow: boolean
} | null {
  const rawLines = rawText.replace(/\r\n?/g, '\n').split('\n')
  const candidateLines: Array<{ lineNo: number; text: string }> = []
  rawLines.forEach((line, idx) => {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('```')) return
    candidateLines.push({ lineNo: idx + 1, text: trimmed })
  })
  if (candidateLines.length === 0) return null

  const chooseDelimiter = (text: string): ',' | '\t' | null => {
    const tabCount = (text.match(/\t/g) ?? []).length
    if (tabCount >= 1) return '\t'
    const commaCount = (text.match(/,/g) ?? []).length
    if (commaCount >= 1) return ','
    return null
  }

  for (let i = 0; i < candidateLines.length; i += 1) {
    const first = candidateLines[i]
    const delimiter = chooseDelimiter(first.text)
    if (!delimiter) continue
    const firstCells = splitDelimitedRow(first.text, delimiter)
    if (firstCells.length < 2) continue

    const hasRecognizedHeader = firstCells.some((cell) => detectColumnKey(cell) !== null)
    const looksLikeDataRow =
      !hasRecognizedHeader &&
      firstCells.length >= 4 &&
      firstCells.length <= 5 &&
      firstCells
        .slice(0, 4)
        .every((cell) => {
          const parsed = parsePositiveDecimalCell(cell, '字段')
          return !parsed.error
        }) &&
      firstCells.slice(0, 4).some((cell) => parsePositiveDecimalCell(cell, '字段').value !== null)

    if (!hasRecognizedHeader && !looksLikeDataRow) continue

    const dataLines: Array<{ lineNo: number; cells: string[] }> = []
    if (looksLikeDataRow) {
      dataLines.push({ lineNo: first.lineNo, cells: firstCells })
    }

    let prevLineNo = first.lineNo
    for (let j = i + 1; j < candidateLines.length; j += 1) {
      const curr = candidateLines[j]
      if (curr.lineNo > prevLineNo + 1) break
      if (!curr.text.includes(delimiter)) break
      const cells = splitDelimitedRow(curr.text, delimiter)
      if (cells.length < 2) break
      dataLines.push({ lineNo: curr.lineNo, cells })
      prevLineNo = curr.lineNo
    }

    if (dataLines.length > 0 || hasRecognizedHeader) {
      return {
        headerCells: firstCells,
        dataLines,
        headerIsDataRow: looksLikeDataRow,
      }
    }
  }
  return null
}

/** 规范化表头文本，去除 Markdown 修饰符、括号单位与空白。 */
function normalizeHeader(cell: string): string {
  return cell
    .replace(/[*_`~]/g, '')
    .replace(/[（(][^）)]*[）)]/g, '')
    .replace(/\s+/g, '')
    .toLowerCase()
}

/** 将单个表头文本识别为标准列键名，无法识别时返回 null。 */
function detectColumnKey(header: string): ColumnKey | null {
  const norm = normalizeHeader(header)
  if (!norm) return null
  if (/(buyprice|买入价|买价|买入价格|目标买价|建仓价|低吸价|补仓价)/.test(norm)) return 'buyPrice'
  if (/(buyqty|buyquantity|buyshares|买入数量|买入股数|买入份额|买量|建仓数量|买股|买份)/.test(norm)) return 'buyQty'
  if (/(sellprice|卖出价|卖价|卖出价格|目标卖价|止盈价|高抛价|减仓价)/.test(norm)) return 'sellPrice'
  if (/(sellqty|sellquantity|sellshares|卖出数量|卖出股数|卖出份额|卖量|止盈数量|卖股|卖份)/.test(norm)) return 'sellQty'
  if (/(status|状态|执行状态|进度)/.test(norm)) return 'status'
  if (/(note|memo|remark|备注|说明|理由|条件|触发条件|逻辑)/.test(norm)) return 'note'
  if (/(tier|level|档位|批次|网格|序号)/.test(norm)) return 'tier'
  return null
}

/** 清洗并解析价格或数量单元格文本，空占位符返回 null，非法数字返回错误说明。 */
function parsePositiveDecimalCell(
  rawCell: string | undefined,
  fieldLabel: string,
): { value: string | null; error?: string } {
  const cleaned = (rawCell ?? '')
    .replace(/[*_`~]/g, '')
    .trim()
  if (EMPTY_PLACEHOLDERS.has(cleaned.toLowerCase())) {
    return { value: null }
  }
  const normalized = cleaned
    .replace(/[¥￥$€£]/g, '')
    .replace(/,/g, '')
    .replace(/(股|份|元|美元|人民币)$/u, '')
    .trim()
  if (EMPTY_PLACEHOLDERS.has(normalized.toLowerCase())) {
    return { value: null }
  }
  if (!/^[+-]?\d+(\.\d+)?$/.test(normalized)) {
    return { value: null, error: `${fieldLabel}「${cleaned}」不是有效数字` }
  }
  const num = Number(normalized)
  if (!Number.isFinite(num) || num <= 0) {
    return { value: null, error: `${fieldLabel}「${cleaned}」必须大于 0` }
  }
  return { value: normalized }
}

/** 解析可选的状态列文本为标准枚举值，未知值默认回退为 PENDING。 */
function parseStatusCell(rawCell: string | undefined): StrategyRowStatus {
  const cleaned = (rawCell ?? '').replace(/[*_`~]/g, '').trim().toLowerCase()
  if (cleaned === 'bought' || cleaned === '已买入' || cleaned === '持仓中') return 'BOUGHT'
  if (cleaned === 'completed' || cleaned === '已完成' || cleaned === '已卖出' || cleaned === '已结清') return 'COMPLETED'
  return 'PENDING'
}

/** 清洗备注文本并去除 Markdown 修饰符，空占位符归一化为 null。 */
function parseNoteCell(rawCell: string | undefined): string | null {
  const cleaned = (rawCell ?? '').replace(/[*_`]/g, '').trim()
  if (EMPTY_PLACEHOLDERS.has(cleaned.toLowerCase())) return null
  return cleaned
}

/** 解析用户粘贴的 Markdown 表格或 CSV/TSV 文本为结构化买卖策略档位及精确行级错误提示。 */
export function parseStrategyMarkdown(rawText: string): StrategyParseResult {
  const trimmed = rawText.trim()
  if (!trimmed) {
    return { rows: [], errors: [], warnings: [] }
  }

  const mdBlock = extractFirstTableBlock(trimmed)
  const csvBlock = mdBlock ? null : extractFirstCsvBlock(trimmed)
  const block = mdBlock ?? csvBlock
  if (!block) {
    return {
      rows: [],
      errors: [
        {
          line: 1,
          message: '未识别到有效的 Markdown 表格或 CSV 文本（Markdown 需含表头与 --- 分隔行；CSV 需用逗号分隔）',
        },
      ],
      warnings: [],
    }
  }

  const warnings: string[] = []
  const columnMap: Partial<Record<ColumnKey, number>> = {}
  const headerIsDataRow = Boolean(csvBlock?.headerIsDataRow)

  if (!headerIsDataRow) {
    block.headerCells.forEach((cell, idx) => {
      const key = detectColumnKey(cell)
      if (key && columnMap[key] === undefined) {
        columnMap[key] = idx
      }
    })
  }

  const recognizedCore =
    columnMap.buyPrice !== undefined ||
    columnMap.buyQty !== undefined ||
    columnMap.sellPrice !== undefined ||
    columnMap.sellQty !== undefined

  if (!recognizedCore) {
    if (block.headerCells.length >= 4 && block.headerCells.length <= 5) {
      columnMap.buyPrice = 0
      columnMap.buyQty = 1
      columnMap.sellPrice = 2
      columnMap.sellQty = 3
      if (block.headerCells.length === 5) columnMap.note = 4
      warnings.push(
        headerIsDataRow
          ? '检测到无表头 CSV 文本，已按默认列顺序（买入价、买入数量、卖出价、卖出数量、备注）解析，请核对预览。'
          : '未匹配到标准中文/英文表头，已按默认列顺序（买入价、买入数量、卖出价、卖出数量、备注）解析，请核对预览。',
      )
    } else {
      return {
        rows: [],
        errors: [
          {
            line: 1,
            message: '未能识别买入价/买入数量/卖出价/卖出数量列名，请确认表头格式或使用固定 AI 提示词重新生成。',
          },
        ],
        warnings: [],
      }
    }
  }

  if (block.dataLines.length === 0) {
    return {
      rows: [],
      errors: [{ line: 1, message: '表格中不包含任何数据行' }],
      warnings,
    }
  }

  const rows: StrategyRowInput[] = []
  const errors: StrategyParseError[] = []

  for (const { lineNo, cells } of block.dataLines) {
    const buyPriceRes =
      columnMap.buyPrice !== undefined
        ? parsePositiveDecimalCell(cells[columnMap.buyPrice], '买入价')
        : { value: null }
    const buyQtyRes =
      columnMap.buyQty !== undefined
        ? parsePositiveDecimalCell(cells[columnMap.buyQty], '买入数量')
        : { value: null }
    const sellPriceRes =
      columnMap.sellPrice !== undefined
        ? parsePositiveDecimalCell(cells[columnMap.sellPrice], '卖出价')
        : { value: null }
    const sellQtyRes =
      columnMap.sellQty !== undefined
        ? parsePositiveDecimalCell(cells[columnMap.sellQty], '卖出数量')
        : { value: null }

    const rowErrors = [
      buyPriceRes.error,
      buyQtyRes.error,
      sellPriceRes.error,
      sellQtyRes.error,
    ].filter((msg): msg is string => Boolean(msg))

    if (rowErrors.length > 0) {
      errors.push({ line: lineNo, message: rowErrors.join('；') })
      continue
    }

    if (
      buyPriceRes.value === null &&
      buyQtyRes.value === null &&
      sellPriceRes.value === null &&
      sellQtyRes.value === null
    ) {
      errors.push({ line: lineNo, message: '该行买入与卖出价格、数量均为空' })
      continue
    }

    const tierText = columnMap.tier !== undefined ? parseNoteCell(cells[columnMap.tier]) : null
    const noteText = columnMap.note !== undefined ? parseNoteCell(cells[columnMap.note]) : null
    let mergedNote: string | null = null
    if (tierText && noteText) {
      mergedNote = `${tierText}：${noteText}`
    } else {
      mergedNote = noteText ?? tierText
    }
    if (mergedNote && mergedNote.length > 256) {
      mergedNote = mergedNote.slice(0, 256)
      warnings.push(`第 ${lineNo} 行备注超过 256 字符，已自动截断。`)
    }

    const status = columnMap.status !== undefined ? parseStatusCell(cells[columnMap.status]) : 'PENDING'

    rows.push({
      buyPrice: buyPriceRes.value,
      buyQty: buyQtyRes.value,
      sellPrice: sellPriceRes.value,
      sellQty: sellQtyRes.value,
      note: mergedNote,
      status,
    })
  }

  return { rows, errors, warnings }
}

/** 将文本复制到系统剪贴板，优先使用 Clipboard API，不支持时降级使用 textarea 选中复制。 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 某些非 HTTPS 局域网环境会拒绝 Clipboard API，继续尝试降级方案。
    }
  }
  if (typeof document === 'undefined') return false
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  textarea.style.top = '0'
  document.body.appendChild(textarea)
  textarea.select()
  let copied = false
  try {
    copied = document.execCommand('copy')
  } catch {
    copied = false
  } finally {
    document.body.removeChild(textarea)
  }
  return copied
}
