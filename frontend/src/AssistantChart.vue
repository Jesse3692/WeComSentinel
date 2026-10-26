<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { eventColor, themeColor } from './theme/chart'
import type { AssistantChartSpec, ChartSeries } from './assistantCharts'

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, SVGRenderer])
const props = defineProps<{ spec: AssistantChartSpec }>()
const el = ref<HTMLElement | null>(null)
const themeVersion = ref(0)
let chart: echarts.ECharts | undefined
let resize: ResizeObserver | undefined
let theme: MutationObserver | undefined
const empty = computed(() => !props.spec.categories.length || props.spec.series.every(series => series.values.every(value => value === 0)))
const height = computed(() => props.spec.kind === 'trend' ? 240 : props.spec.kind === 'composition' ? 130
  : Math.max(210, props.spec.categories.length * (props.spec.kind === 'comparison' ? 58 : 38) + 48))
function color(tone?: string) {
  if (tone === 'previous') return themeColor('--text-secondary')
  if (tone === 'current') return themeColor('--action')
  if (tone === 'employee') return eventColor('group_removed')
  if (tone === 'unknown') return themeColor('--text-secondary')
  return eventColor(tone || '')
}
const palette = computed(() => { void themeVersion.value; return props.spec.series.map(series => color(series.tone)) })
function legendLabel(series: ChartSeries) {
  if (props.spec.kind !== 'composition') return series.name
  const total = props.spec.series.reduce((sum, row) => sum + row.values[0]!, 0)
  return `${series.name}：${series.values[0]} 人次${total ? `（${(series.values[0]! / total * 100).toFixed(1)}%）` : ''}`
}
function draw() {
  if (!el.value || empty.value || !el.value.clientWidth) return
  chart ??= echarts.init(el.value, undefined, { renderer: 'svg' })
  const spec = props.spec, line = spec.kind === 'trend', stacked = spec.kind === 'composition'
  const maximum = Math.max(0, ...spec.series.flatMap(series => series.values))
  const valueSpace = Math.max(28, String(maximum).length * 7 + 12)
  const compositionTotal = stacked ? spec.series.reduce((sum, series) => sum + series.values[0]!, 0) : 0
  const text = themeColor('--text-secondary'), border = themeColor('--border')
  const axis = { axisLabel: { color: text, fontSize: 12 }, axisTick: { show: false }, axisLine: { lineStyle: { color: border } } }
  const category = { ...axis, type: 'category' as const, data: spec.categories,
    ...(line ? { boundaryGap: false, axisLabel: { color: text, fontSize: 12, hideOverlap: true, formatter: (value: string) => value.slice(5) } }
      : { inverse: true, axisLabel: { color: text, fontSize: 12, width: 86, overflow: 'truncate' as const } }) }
  const numeric = { ...axis, type: 'value' as const, min: 0, minInterval: 1, name: '人次', nameTextStyle: { color: text },
    splitLine: { lineStyle: { color: border } } }
  chart.setOption({
    animation: false,
    color: spec.series.map(series => color(series.tone)),
    // Rich text tooltips do not interpret customer/group names as HTML.
    tooltip: { trigger: 'axis', confine: true, renderMode: 'richText', textStyle: { fontSize: 12 },
      backgroundColor: themeColor('--bg-card'), borderColor: border },
    grid: { left: line ? Math.max(38, valueSpace) : 96, right: line || stacked ? 28 : valueSpace, top: 30, bottom: 28 },
    xAxis: line ? category : numeric,
    yAxis: line ? numeric : category,
    series: spec.series.map(series => ({
      name: series.name, type: line ? 'line' : 'bar', data: series.values,
      ...(stacked ? { stack: 'directions' } : {}),
      ...(line ? { showSymbol: spec.categories.length <= 31, symbolSize: 5, connectNulls: false }
        : { barMaxWidth: 18, labelLayout: { hideOverlap: true },
          label: { show: true, position: stacked ? 'inside' : 'right', color: themeColor(stacked ? '--bg-card' : '--text-primary'),
          fontSize: 12, formatter: (params: any) => stacked && (!params.value || params.value / compositionTotal < 0.1) ? '' : String(params.value) } }),
    })),
  }, { notMerge: true })
}
watch(() => props.spec, draw, { deep: true, flush: 'post' })
onMounted(() => {
  if (!el.value) return
  draw()
  resize = new ResizeObserver(() => { chart?.resize(); draw() })
  resize.observe(el.value)
  theme = new MutationObserver(() => { themeVersion.value++; draw() })
  theme.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] })
})
onBeforeUnmount(() => { resize?.disconnect(); theme?.disconnect(); chart?.dispose() })
</script>

<template>
  <figure class="assistant-chart" :data-chart-kind="spec.kind">
    <figcaption>{{spec.title}}</figcaption>
    <p v-if="spec.range.length" class="chart-note">{{spec.range.join(' 至 ')}}（北京时间）</p>
    <div class="chart-legend"><span v-for="(series,index) in spec.series" :key="series.name"><i :style="{background:palette[index]}"></i>{{legendLabel(series)}}</span></div>
    <p v-if="empty" class="chart-empty">{{spec.categories.length ? '本次查询各项均为 0 人次。' : '当前页没有可绘制的数据。'}}</p>
    <div ref="el" v-show="!empty" class="chart-canvas" :style="{height:height+'px'}" role="img" :aria-label="spec.title+'，单位人次；完整数值见下方图表数据。'"></div>
    <p class="chart-note">{{spec.note}}</p>
    <details class="chart-values"><summary>查看图表数据</summary><table><caption class="chart-table-caption">{{spec.title}}（人次）</caption><thead><tr><th scope="col">{{spec.kind==='trend'?'日期':'项目'}}</th><th v-for="series in spec.series" :key="series.name" scope="col">{{series.name}}</th></tr></thead><tbody><tr v-for="(category,index) in spec.categories" :key="index"><th scope="row">{{category}}</th><td v-for="series in spec.series" :key="series.name">{{series.values[index]}}</td></tr></tbody></table></details>
  </figure>
</template>

<style scoped>
.assistant-chart{margin:16px 0;min-width:0;border-top:1px solid var(--border);padding-top:14px}.assistant-chart figcaption{font-size:14px;font-weight:600}.chart-note{color:var(--text-secondary);font-size:12px;line-height:1.75;margin:8px 0;overflow-wrap:anywhere}.chart-legend{display:flex;gap:8px 14px;flex-wrap:wrap;font-size:12px;margin:10px 0}.chart-legend span{display:inline-flex;align-items:center;gap:6px}.chart-legend i{width:10px;height:10px;flex-shrink:0}.chart-canvas{width:100%;min-width:0}.chart-empty{padding:16px 0;color:var(--text-secondary);font-size:13px}.chart-values{font-size:12px}.chart-values summary{color:var(--action);padding:8px 0;cursor:pointer}.chart-values table{width:100%;table-layout:fixed;border-collapse:collapse;margin-top:8px}.chart-values th,.chart-values td{border-bottom:1px solid var(--border);padding:8px 3px;overflow-wrap:anywhere;text-align:right;font-variant-numeric:tabular-nums;font-weight:400}.chart-values th:first-child{text-align:left}.chart-table-caption{text-align:left;color:var(--text-secondary);padding:6px 0}
@media(pointer:coarse){.chart-values summary{min-height:44px}}
</style>
