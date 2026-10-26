const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('../frontend/node_modules/typescript')
const source = fs.readFileSync(path.join(__dirname, '../frontend/src/assistantCharts.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const context = { exports: {}, Intl, Date }
vm.runInNewContext(compiled, context)
const { assistantCharts, assistantTrendQuery } = context.exports
const plain = value => JSON.parse(JSON.stringify(value))

const response = {
  range: ['2026-10-20', '2026-10-26'], previous_range: ['2026-10-13', '2026-10-19'],
  cards: [
    { type: 'contact_added', value: 19, delta: -48 },
    { type: 'contact_deleted', value: 3, delta: -7 },
    { type: 'group_left', value: 5, delta: -11 },
    { type: 'group_removed', value: 1, delta: -3 },
  ],
  deletion_directions: { customer_deleted_employee: 2, employee_deleted_customer: 1, unknown: 0 },
  unresolved_count: 4, rankings: {}, answer: '新增客户99999人，不能据此绘图',
}
const charts = assistantCharts(response)
assert.deepEqual(plain(charts[0].series.map(series => series.values)), [[67, 10, 16, 4], [19, 3, 5, 1]])
assert.deepEqual(plain(charts[1].series.map(series => series.values)), [[2], [1], [0]])
assert.match(charts[0].note, /待核实：4.*内部员工/)
assert.ok(!JSON.stringify(charts).includes('99999'))
assert.deepEqual(plain(assistantTrendQuery(response)), { capability: 'trend', start_date: '2026-10-20', end_date: '2026-10-26', offset: 0, limit: 100 })
assert.equal(assistantCharts({ ...response, event_id: 5 }).length, 0)
assert.equal(assistantTrendQuery({ ...response, event_id: 5 }), null)
assert.equal(assistantCharts({ result_type: 'groups', groups: { total: 10 } }).length, 0)
assert.equal(assistantCharts({ answer: '新增19人次，减少48人次' }).length, 0)

// Missing, negative, nonnumeric or inconsistent values cannot become fabricated bars.
for (const cards of [
  [{ type: 'contact_added', value: 19 }],
  [{ type: 'contact_added', value: 1, delta: 2 }],
  [{ type: 'contact_added', value: -1, delta: 0 }],
  [{ type: 'contact_added', value: '19', delta: -48 }],
  [{ type: 'contact_added', value: NaN, delta: 0 }],
]) assert.equal(assistantCharts({ cards }).length, 0)
assert.deepEqual(plain(assistantCharts({ cards: [{ type: 'contact_added', value: 5, delta: 5 }] })[0].series.map(series => series.values)), [[0], [5]])
assert.deepEqual(plain(assistantCharts({ ...response, deletion_directions: { ...response.deletion_directions, unknown: 2 } })[1].series[2].values), [2])
assert.equal(assistantCharts({ ...response, deletion_directions: { customer_deleted_employee: 2 } }).length, 1)
const zero = assistantCharts({ ...response, cards: response.cards.map(row => ({ ...row, value: 0, delta: 0 })), deletion_directions: { customer_deleted_employee: 0, employee_deleted_customer: 0, unknown: 0 } })
assert.equal(zero.length, 2)
assert.ok(zero.every(chart => chart.series.every(series => series.values.every(value => value === 0))))

const stats = assistantCharts({ dataset: { page_query: { capability: 'statistics' }, range: response.range, items: [{ type: '其他 / 待核实', current: 4, previous: 2 }], note: '与上个等长区间比较' } })
assert.deepEqual(plain(stats[0].series.map(series => series.values)), [[2], [4]])
const days = [
  { date: '2026-10-20', contact_added: 3, contact_deleted: 1, group_left: 2, group_removed: 0, group_change_unknown: 4 },
  { date: '2026-10-21', contact_added: 0, contact_deleted: 0, group_left: 0, group_removed: 1, group_change_unknown: 0 },
]
const trend = assistantCharts({ dataset: { page_query: { capability: 'trend' }, range: response.range, items: days, total: 7 } })[0]
assert.deepEqual(plain(trend.categories), ['2026-10-20', '2026-10-21'])
assert.equal(trend.series.length, 5)
assert.deepEqual(plain(trend.series[4].values), [4, 0])
assert.match(trend.note, /当前页 2 \/ 7 天/)
assert.equal(assistantTrendQuery({ ...response, dataset: { page_query: { capability: 'trend' } } }), null)
const ranks = assistantCharts({ dataset: { title: '主动退群排名', page_query: { capability: 'rankings' }, range: response.range, offset: 20, total: 40,
  items: Array.from({ length: 20 }, (_, index) => ({ rank: index + 21, name: '<img onerror=alert(1)>', tenant: `主体${index % 2}`, quantity: 20 - index })) } })[0]
assert.equal(ranks.categories.length, 10)
assert.match(ranks.categories[0], /^21\./)
assert.match(ranks.note, /当前页前 10 条，共 40 条/)
assert.ok(ranks.categories[0].includes('主体0'))
assert.equal(assistantCharts({ dataset: { page_query: { capability: 'employees' }, items: [{ name: '员工', quantity: 99 }] } }).length, 0)
assert.equal(assistantCharts({ dataset: { page_query: { capability: 'rankings' }, items: [], total: 0 } })[0].categories.length, 0)
console.log('PASS: assistant chart quantities, deletion directions, no prose parsing, zero/missing values, event isolation, trend page scope, pending category, ranking page scope and related query dates')
