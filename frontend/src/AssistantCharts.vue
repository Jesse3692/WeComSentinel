<script setup lang="ts">
import { computed, ref } from 'vue'
import AssistantChart from './AssistantChart.vue'
import { assistantCharts, assistantTrendQuery } from './assistantCharts'
type Row = Record<string, any>
const props = defineProps<{ data: Row; busy: boolean }>()
const emit = defineEmits<{ query: [query: Row] }>()
const visible = ref(true)
const charts = computed(() => assistantCharts(props.data))
const trendQuery = computed(() => assistantTrendQuery(props.data))
</script>

<template>
  <section v-if="charts.length" class="assistant-charts" aria-label="数据图表">
    <div class="chart-actions"><strong>数据图表</strong><button class="text-btn" :aria-expanded="visible" @click="visible=!visible">{{visible?'收起图表':'展开图表'}}</button></div>
    <template v-if="visible"><AssistantChart v-for="chart in charts" :key="chart.id" :spec="chart" /><button v-if="trendQuery" class="secondary" :disabled="busy" @click="emit('query',trendQuery)">查看每日趋势图</button></template>
  </section>
</template>

<style scoped>
.assistant-charts{margin-top:18px;min-width:0}.chart-actions{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:13px}.chart-actions button{min-height:36px;color:var(--action)}.assistant-charts>.secondary{font-size:12px;white-space:normal;min-height:40px}
</style>
