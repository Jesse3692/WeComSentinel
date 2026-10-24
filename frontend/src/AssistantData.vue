<script setup lang="ts">
import { ref } from 'vue'
type Row = Record<string, any>
const props=defineProps<{data:Row;busy:boolean}>()
const emit=defineEmits<{query:[query:Row];event:[id:number]}>()
const keyword=ref(props.data.page_query.keyword || '')
const previews=ref<number[]>([])
const filterable=new Set(['tenants','employees','customers','groups','group_detail','group_members','customer_followers','events','statistics','trend','rankings','users','tenant_settings','audits'])
function filter(){emit('query',{...props.data.page_query,keyword:keyword.value.trim(),entity_id:'',offset:0,cursor:null})}
function value(row:Row,key:string){const cell=row[key];return cell===null || cell===undefined || cell===''?'未提供':typeof cell==='boolean'?(cell?'是':'否'):typeof cell==='object'?'未提供':String(cell)}
function preview(id:number){previews.value=previews.value.includes(id)?previews.value.filter(value=>value!==id):[...previews.value,id]}
</script>

<template>
  <section class="assistant-dataset" :aria-label="data.title">
    <h3>{{data.title}}</h3>
    <p class="data-note">{{data.note}}</p>
    <p v-if="data.range" class="data-note">区间：{{data.range.join(' 至 ')}}</p>
    <div v-if="data.filters?.length" class="data-applied"><span v-for="filter in data.filters" :key="filter.label">{{filter.label}}：{{filter.value}}</span></div>
    <form v-if="filterable.has(data.page_query.capability)" class="data-search" @submit.prevent="filter"><label>关键词筛选<input v-model="keyword" maxlength="100" aria-label="数据关键词筛选" placeholder="名称、标识或明细关键词"></label><button class="secondary" :disabled="busy">查询</button></form>
    <p class="data-note">共 {{data.total}} 条<span v-if="data.total_quantity!==null"> · {{data.total_quantity}} 人次</span></p>
    <div v-if="!data.items.length" class="data-empty">当前条件下没有已保存的记录。</div>
    <article v-for="(row,index) in data.items" :key="index" class="data-record">
      <strong>{{value(row,data.columns[0].key)}}</strong>
      <dl><template v-for="column in data.columns.slice(1,4)" :key="column.key"><dt>{{column.label}}</dt><dd>{{value(row,column.key)}}</dd></template></dl>
      <details v-if="data.columns.length>4"><summary>更多字段</summary><dl><template v-for="column in data.columns.slice(4)" :key="column.key"><dt>{{column.label}}</dt><dd>{{value(row,column.key)}}</dd></template></dl></details>
      <div v-if="row.actions?.length || row.event_id || row.media_id" class="data-actions"><button v-for="(action,i) in row.actions" :key="i" class="text-btn" :disabled="busy" @click="emit('query',action.query)">{{action.label}} →</button><button v-if="row.event_id" class="text-btn" :disabled="busy" @click="emit('event',row.event_id)">事件详情 →</button><button v-if="row.media_id" class="text-btn" @click="preview(row.media_id)">{{previews.includes(row.media_id)?'收起图片':'查看图片'}}</button></div>
      <img v-if="row.media_id && previews.includes(row.media_id)" class="data-avatar" :src="'/api/v1/media/'+row.media_id" loading="lazy" alt="已授权的本地头像">
    </article>
    <nav v-if="data.next_query || data.previous_query" class="data-pages" aria-label="助手数据分页"><button class="secondary" :disabled="busy || !data.previous_query" @click="emit('query',data.previous_query)">上一页数据</button><span>第 {{Math.floor(data.offset/data.limit)+1}} 页</span><button class="secondary" :disabled="busy || !data.next_query" @click="emit('query',data.next_query)">下一页数据</button></nav>
    <p class="data-note">数据来源：{{data.source}}</p>
  </section>
</template>

<style scoped>
.assistant-dataset{margin-top:16px;min-width:0}.assistant-dataset h3{font-size:15px}.data-note{color:var(--text-secondary);font-size:12px;line-height:1.7;margin:10px 0;overflow-wrap:anywhere}.data-search{display:flex;gap:8px;align-items:flex-end;margin:14px 0}.data-search label{min-width:0;flex:1}.data-search input{width:100%;min-width:0}.data-search button{padding:9px 12px;flex-shrink:0}.data-record{padding:14px 0;border-bottom:1px solid var(--border);overflow-wrap:anywhere}.data-record>strong{font-size:14px}.data-record dl{display:grid;grid-template-columns:minmax(64px,92px) minmax(0,1fr);gap:6px 10px;margin:10px 0;font-size:12px}.data-record dt{color:var(--text-secondary)}.data-record dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.data-record summary{color:var(--action);font-size:12px;cursor:pointer;padding:6px 0}.data-actions{display:flex;gap:8px;flex-wrap:wrap}.data-actions button{color:var(--action);min-height:36px;overflow-wrap:anywhere}.data-pages{display:flex;gap:8px;justify-content:space-between;align-items:center;flex-wrap:wrap;margin-top:14px;font-size:12px}.data-pages button{font-size:12px;padding:8px}.data-empty{color:var(--text-secondary);padding:14px 0}.data-avatar{display:block;max-width:160px;max-height:160px;object-fit:contain;margin-top:10px}.data-applied{display:flex;gap:6px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary)}.data-applied span{padding:4px 6px;background:var(--bg-subtle);border-radius:4px;overflow-wrap:anywhere}
@media(pointer:coarse){.data-actions button,.data-record summary{min-height:44px}.data-search input{font-size:16px}}
</style>
