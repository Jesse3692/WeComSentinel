// Chart values come only from structured API fields, never from model prose.
type Row = Record<string, any>
export type ChartSeries = { name: string; values: number[]; tone?: string }
export type AssistantChartSpec = {
  id: string
  title: string
  kind: 'comparison' | 'composition' | 'trend' | 'ranking'
  categories: string[]
  series: ChartSeries[]
  note: string
  range: string[]
}

const labels: Record<string, string> = {
  contact_added: '新增联系人', contact_deleted: '删除联系人',
  group_left: '主动退群', group_removed: '被移出群', group_change_unknown: '其他 / 待核实',
}
const quantityNote = '单位：人次 / 关系变更人次，不等于独立客户数，各类不能合并为独立流失人数。'
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const signedCount = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value)
const rangeOf = (value: unknown): string[] => Array.isArray(value) && value.length === 2
  && value.every(day => typeof day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day)) ? value : []

function periodNote(range: string[]) {
  const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(new Date())
  return range.length === 2 && range[0] <= today && today <= range[1]
    ? '包含今天，数据截至本次查询；上期为完整区间。' : ''
}

function comparison(id: string, rows: Row[], range: string[], previousRange: string[], note = ''): AssistantChartSpec {
  return {
    id, title: '本期与上期对比', kind: 'comparison', range,
    categories: rows.map(row => row.label),
    series: [
      { name: '上期', values: rows.map(row => row.previous), tone: 'previous' },
      { name: '本期', values: rows.map(row => row.current), tone: 'current' },
    ],
    note: [previousRange.length ? `上期：${previousRange.join(' 至 ')}。` : '', note, periodNote(range), quantityNote].filter(Boolean).join(' '),
  }
}

export function assistantCharts(data: Row): AssistantChartSpec[] {
  // A single event must never be illustrated with unrelated interval aggregates.
  if (data.event_id) return []
  const charts: AssistantChartSpec[] = []
  const dataset = data.dataset
  if (dataset) {
    const capability = dataset.page_query?.capability
    const range = rangeOf(dataset.range)
    const items: Row[] = Array.isArray(dataset.items) ? dataset.items : []
    if (capability === 'statistics') {
      const rows = items.filter(row => count(row.current) && count(row.previous))
        .map(row => ({ label: row.type, current: row.current, previous: row.previous }))
      if (rows.length) charts.push(comparison('statistics', rows, range, [], dataset.note || ''))
    } else if (capability === 'trend') {
      // Render the actual page only: never synthesize daily values from a period total.
      const rows = items.filter(row => typeof row.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(row.date)
        && Object.keys(labels).every(key => count(row[key])))
      charts.push({
        id: 'trend', title: '每日事件趋势', kind: 'trend', range,
        categories: rows.map(row => row.date),
        series: Object.entries(labels).map(([key, name]) => ({ name, values: rows.map(row => row[key]), tone: key })),
        note: `仅展示当前页 ${rows.length} / ${dataset.total} 天${rows.length ? `（${rows[0]!.date} 至 ${rows[rows.length - 1]!.date}）` : ''}；翻页查看其余日期。其他 / 待核实可能包含内部员工。${quantityNote}`,
      })
    } else if (capability === 'rankings') {
      const rows = items.filter(row => count(row.quantity)).slice(0, 10)
      charts.push({
        id: 'ranking', title: dataset.title || '人次排名', kind: 'ranking', range,
        categories: rows.map((row, index) => `${row.rank ?? (dataset.offset || 0) + index + 1}. ${row.name}（${row.tenant}）`),
        series: [{ name: '人次', values: rows.map(row => row.quantity), tone: 'current' }],
        note: `展示当前页前 ${rows.length} 条，共 ${dataset.total} 条；完整当前页记录见数据明细，翻页查看其他排名。${dataset.note || ''} ${quantityNote}`,
      })
    }
    return charts
  }
  if (data.result_type === 'groups') return []
  const range = rangeOf(data.range)
  const cards: Row[] = Array.isArray(data.cards) ? data.cards : []
  const rows = cards.filter(row => labels[row.type] && count(row.value) && signedCount(row.delta)
    && count(row.value - row.delta))
    .map(row => ({ label: labels[row.type], current: row.value, previous: row.value - row.delta }))
  if (rows.length) {
    const unresolved = count(data.unresolved_count)
      ? `其他 / 待核实：${data.unresolved_count} 人次（可能包含内部员工），未混入四类指标。` : ''
    charts.push(comparison('comparison', rows, range, rangeOf(data.previous_range), unresolved))
  }
  const directions = data.deletion_directions
  if (rows.length && directions && ['customer_deleted_employee', 'employee_deleted_customer', 'unknown'].every(key => count(directions[key]))) {
    charts.push({
      id: 'deletion-directions', title: '本期删除方向构成', kind: 'composition', range,
      categories: ['删除关系'],
      series: [
        { name: '客户删除员工', values: [directions.customer_deleted_employee], tone: 'current' },
        { name: '员工删除客户', values: [directions.employee_deleted_customer], tone: 'employee' },
        { name: '方向未提供', values: [directions.unknown], tone: 'unknown' },
      ],
      note: `保留方向未提供的人次；删除方向反映操作记录，不能据此确认客户离开动机。${quantityNote}`,
    })
  }
  const rankings = data.rankings
  if (rankings && typeof rankings === 'object') {
    for (const [kind, value] of Object.entries(rankings)) {
      if (!labels[kind] || !Array.isArray(value)) continue
      const items = value.filter(row => count(row.count)).slice(0, 5)
      charts.push({
        id: `ranking-${kind}`, title: `${labels[kind]} Top 5`, kind: 'ranking', range,
        categories: items.map((row, index) => `${index + 1}. ${row.name}（主体 #${row.tenant_id}）`),
        series: [{ name: '人次', values: items.map(row => row.count), tone: kind }],
        note: quantityNote,
      })
    }
  }
  return charts
}

export function assistantTrendQuery(data: Row): Row | null {
  const range = rangeOf(data.range)
  return !data.event_id && !data.dataset && Array.isArray(data.cards) && data.cards.length && range.length
    ? { capability: 'trend', start_date: range[0], end_date: range[1], offset: 0, limit: 100 }
    : null
}
