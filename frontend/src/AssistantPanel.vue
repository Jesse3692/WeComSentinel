<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from './api'
import AssistantData from './AssistantData.vue'

type Row = Record<string, any>
const props = defineProps<{
  open: boolean; tenantId: string; tenantName: string; startDate: string; endDate: string
  eventId: number | null; userId: number
}>()
const emit = defineEmits<{close: []; event: [id: number]}>()
const messages = ref<{id: number; question: string; data?: Row; error?: string}[]>([])
const text = ref(''), busy = ref(false), mode = ref('rules'), model = ref(''), enabled = ref(true)
const input = ref<HTMLTextAreaElement | null>(null), log = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
let controller: AbortController | undefined, version = 0, sequence = 0
let originFocus: HTMLElement | null = null
const overlay = ref(matchMedia('(max-width: 1100px)').matches)
const viewportHeight = ref(window.visualViewport?.height || window.innerHeight)
const viewportTop = ref(window.visualViewport?.offsetTop || 0)
let locked = false, savedOverflow = ''
function syncViewport() {
  overlay.value=matchMedia('(max-width: 1100px)').matches
  viewportHeight.value=window.visualViewport?.height || window.innerHeight
  viewportTop.value=window.visualViewport?.offsetTop || 0
  if(props.open && overlay.value && !locked){savedOverflow=document.body.style.overflow;document.body.style.overflow='hidden';locked=true}
  if((!props.open || !overlay.value) && locked){document.body.style.overflow=savedOverflow;locked=false}
}
const labels: Record<string,string> = {contact_added:'新增联系人',contact_deleted:'删除联系人',group_left:'主动退群',group_removed:'被移出群'}
const scope = computed(()=>[props.userId,props.tenantId,props.startDate,props.endDate,props.eventId].join('|'))
const shortcuts = computed(()=>props.eventId
  ? ['解释这条事件的识别依据', '这条事件计入什么统计？']
  : ['当前主体中有哪些群', '对比当前区间的客户变化', '生成当前区间周报', '查看当前区间排名'])
const groupSearches = ref<Record<number,string>>({})
const availableCapabilities=ref<Row[]>([]), dataChoice=ref('')

function cancel() { version++; controller?.abort(); controller=undefined; busy.value=false }
function reset() {cancel();messages.value=[];text.value='';groupSearches.value={}}
function close() {cancel();emit('close')}
function newConversation() {reset();void nextTick(()=>input.value?.focus())}
async function scroll() {await nextTick();log.value?.scrollTo({top:log.value.scrollHeight,behavior:'auto'})}
async function send(question = text.value.trim(), groupQuery:Row = {}) {
  if(!question || busy.value || !enabled.value)return
  const history=messages.value.slice(-6).map(item=>item.question)
  const contextQuery=messages.value[messages.value.length-1]?.data?.query || null
  // Retain a bounded conversation only in this component, never in browser storage.
  if(messages.value.length>=20)messages.value.splice(0,messages.value.length-19)
  const item={id:++sequence,question} as {id:number;question:string;data?:Row;error?:string}
  messages.value.push(item);text.value='';busy.value=true
  const current=++version, currentScope=scope.value
  controller=new AbortController()
  void scroll()
  try {
    const data=await api('/assistant/chat', {message:question,tenant_id:props.tenantId,
      start_date:props.startDate||null,end_date:props.endDate||null,event_id:props.eventId,history,context_query:contextQuery,...groupQuery},'POST',true,controller.signal)
    if(current!==version || currentScope!==scope.value)return
    messages.value.find(row=>row.id===item.id)!.data=data
    if(data.groups)groupSearches.value[item.id]=data.groups.keyword
    if(!['groups','data'].includes(data.result_type))mode.value=data.mode
  } catch(error:any) {
    if(current!==version || error.name==='AbortError')return
    if(error.status===401 || error.status===403 || error.status===404) {
      messages.value=[]
      if(error.status===404)enabled.value=false
    }
    const target=messages.value.find(row=>row.id===item.id)
    if(target)target.error=error.message
    else messages.value.push({id:++sequence,question,error:error.message})
  } finally {if(current===version){busy.value=false;controller=undefined;void scroll()}}
}
function groupPage(item: {data?:Row}, offset:number) {
  if(!item.data)return
  const groups=item.data.groups
  void send('查看群列表（第 '+(Math.floor(offset/groups.limit)+1)+' 页）',
    {query_type:'groups',group_keyword:groups.keyword,group_offset:offset,group_limit:groups.limit})
}
function filterGroups(id:number) {
  void send('查询群列表',{query_type:'groups',group_keyword:groupSearches.value[id]||'',group_offset:0})
}
function sendData(query:Row) {
  if(!query)return
  const name=availableCapabilities.value.find(item=>item.id===query.capability)?.name || '数据'
  void send('查询'+name,{query,event_id:null})
}
function chooseData() {if(dataChoice.value)sendData({capability:dataChoice.value})}
async function retry(item: {id:number;question:string}) {
  messages.value=messages.value.filter(row=>row.id!==item.id)
  await send(item.question)
}
async function loadConfig() {
  const currentScope=scope.value
  try {const config=await api('/assistant/config');if(currentScope!==scope.value)return;enabled.value=config.enabled;mode.value=config.mode;model.value=config.model||'';availableCapabilities.value=config.capabilities||[]}
  catch(error:any){if(error.status===401 || error.status===403){reset();close()} }
}
watch(scope,()=>{reset();if(props.open){void loadConfig();if(props.eventId)void send('解释这条事件的识别依据')}})
watch(()=>props.open,async(open)=>{
  syncViewport()
  if(open){originFocus=document.activeElement as HTMLElement;await nextTick();input.value?.focus();void loadConfig();if(props.eventId && !messages.value.length)void send('解释这条事件的识别依据')}
  else {cancel();await nextTick();originFocus?.focus({preventScroll:true})}
})
function keyboard(event:KeyboardEvent) {
  if(event.key==='Escape'){event.preventDefault();close()}
  if(event.key!=='Tab' || !matchMedia('(max-width: 1100px)').matches)return
  const nodes=panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled),textarea:not(:disabled),a[href]')
  if(!nodes?.length)return
  const first=nodes[0],last=nodes[nodes.length-1]
  if(event.shiftKey && document.activeElement===first){event.preventDefault();last.focus()}
  else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus()}
}
function submitKey(event:KeyboardEvent) {
  if(event.key==='Enter' && !event.shiftKey && !event.isComposing){event.preventDefault();void send()}
}
onMounted(()=>{syncViewport();window.addEventListener('resize',syncViewport);window.visualViewport?.addEventListener('resize',syncViewport);window.visualViewport?.addEventListener('scroll',syncViewport);if(props.open){void loadConfig();input.value?.focus()}})
onBeforeUnmount(()=>{cancel();window.removeEventListener('resize',syncViewport);window.visualViewport?.removeEventListener('resize',syncViewport);window.visualViewport?.removeEventListener('scroll',syncViewport);if(locked)document.body.style.overflow=savedOverflow})
</script>

<template>
  <div v-if="open" class="assistant-backdrop" @click="close" aria-hidden="true"></div>
  <section v-if="open" ref="panel" class="assistant-panel" :role="overlay?'dialog':'region'" :aria-modal="overlay?true:undefined" :style="{'--assistant-height':viewportHeight+'px','--assistant-top':viewportTop+'px'}" aria-label="客户分析助手" @keydown="keyboard">
    <header class="assistant-header">
      <div><h2>✦ 客户分析助手</h2><p class="muted">{{mode==='model' ? model || '模型分析' : '规则查询模式'}}</p></div>
      <div class="assistant-header-actions"><button class="text-btn" :disabled="busy" @click="newConversation">新对话</button><button class="assistant-close secondary" @click="close" aria-label="关闭助手">×</button></div>
    </header>
    <div class="assistant-scope">{{tenantName}} · {{startDate && endDate ? startDate+' 至 '+endDate : '默认近 7 天'}}<span v-if="eventId"> · 事件 #{{eventId}}</span></div>
    <div ref="log" class="assistant-log" role="log" aria-live="polite" aria-relevant="additions text">
      <div v-if="!messages.length" class="assistant-welcome"><h3>想了解哪些客户变化？</h3><p>我可以查询授权范围内的数据、解释事件，或生成报告草稿。</p><p class="muted">回答会附上统计区间和数据依据。</p></div>
      <article v-for="item in messages" :key="item.id" class="assistant-exchange">
        <p class="assistant-question">{{item.question}}</p>
        <div v-if="item.data" class="assistant-answer">
          <p v-if="item.data.warning" class="notice" role="status">{{item.data.warning}}</p>
          <div v-if="item.data.event_explanation && item.data.mode==='model'" class="assistant-event-check"><strong>系统识别依据</strong><p>{{item.data.event_explanation}}</p></div>
          <div class="assistant-answer-label">{{['groups','data'].includes(item.data.result_type)?'数据查询':item.data.mode==='model'?'模型分析':'规则查询结果'}}</div>
          <p class="assistant-answer-text">{{item.data.answer}}</p>
          <AssistantData v-if="item.data.dataset" :data="item.data.dataset" :busy="busy" @query="sendData" @event="emit('event',$event)" />
          <section v-if="item.data.groups" class="assistant-groups" aria-label="当前主体群列表">
            <form class="assistant-group-filter" @submit.prevent="filterGroups(item.id)"><label :for="'group-keyword-'+item.id">群名包含</label><div><input :id="'group-keyword-'+item.id" v-model="groupSearches[item.id]" maxlength="100" placeholder="输入群名称关键词"><button class="secondary" :disabled="busy">筛选</button></div></form>
            <p class="muted">共 {{item.data.groups.total}} 个群<span v-if="item.data.groups.keyword"> · 关键词：{{item.data.groups.keyword}}</span></p>
            <ol :start="item.data.groups.offset+1"><li v-for="group in item.data.groups.items" :key="group.tenant_id+':'+group.id"><strong>{{group.name}}</strong><p>{{group.tenant_name}}</p><small>资料同步：{{new Date(group.synced_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}}</small><div class="assistant-group-actions"><button class="text-btn" :disabled="busy" @click="sendData({capability:'group_detail',entity_id:group.id,entity_tenant_id:group.tenant_id})">群详情 →</button><button class="text-btn" :disabled="busy" @click="sendData({capability:'group_members',entity_id:group.id,entity_tenant_id:group.tenant_id})">群成员 →</button></div></li></ol>
            <p v-if="!item.data.groups.items.length" class="muted">当前页没有群资料</p>
            <div class="assistant-group-pages"><button class="secondary" :disabled="busy || item.data.groups.offset===0" @click="groupPage(item,Math.max(0,item.data.groups.offset-item.data.groups.limit))">上一页群列表</button><span>第 {{Math.floor(item.data.groups.offset/item.data.groups.limit)+1}} 页</span><button class="secondary" :disabled="busy || !item.data.groups.has_more" @click="groupPage(item,item.data.groups.next_offset)">下一页群列表</button></div>
          </section>
          <div v-if="!item.data.event_id" class="assistant-cards"><div v-for="card in item.data.cards" :key="card.type"><span>{{labels[card.type]}}</span><strong>{{card.value}} <small>人次</small></strong><small>较上期 {{card.delta>0?'+':''}}{{card.delta}}</small></div></div>
          <div v-for="(rows,kind) in item.data.rankings" :key="kind" class="assistant-rank"><strong>{{labels[kind]}} Top 5</strong><p v-if="!rows.length" class="muted">当前区间无排名数据</p><p v-for="(row,index) in rows" :key="row.tenant_id+':'+row.id">{{Number(index)+1}}. {{row.name}} <span>{{row.count}} 人次</span></p></div>
          <div class="assistant-citation"><template v-if="item.data.groups">数据来源：本地已同步群资料 · 不按统计日期筛选<br></template><template v-else-if="item.data.dataset"></template><template v-else-if="item.data.event_id">事件时间：{{new Date(item.data.event_occurred_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}}<br></template><template v-else>统计区间：{{item.data.range.join(' 至 ')}}<br>对比区间：{{item.data.previous_range.join(' 至 ')}}<br></template>查询时间：{{new Date(item.data.updated_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}}</div>
          <div v-if="item.data.sources.length" class="assistant-sources"><p class="muted">{{item.data.sources_label}}</p><button v-for="source in item.data.sources" :key="source.event_id" class="text-btn" @click="emit('event',source.event_id)">{{source.label}} →</button></div>
        </div>
        <div v-else-if="item.error" class="error" role="alert">{{item.error}} <button class="text-btn" :disabled="busy" @click="retry(item)">重试</button></div>
        <p v-else class="assistant-progress" role="status">{{busy?'正在查询数据并生成回答…':'本次生成已停止'}} </p>
      </article>
    </div>
    <div class="assistant-bottom"><p v-if="!enabled" class="notice">助手未启用，请联系管理员。</p><div class="assistant-shortcuts"><button v-for="question in shortcuts" :key="question" class="secondary" :disabled="busy || !enabled" @click="send(question)">{{question}}</button></div>
      <form v-if="availableCapabilities.length" class="assistant-capability-picker" @submit.prevent="chooseData"><label for="assistant-capability">查询内容</label><select id="assistant-capability" v-model="dataChoice" aria-label="助手查询能力"><option value="">选择数据类型…</option><option v-for="cap in availableCapabilities" :key="cap.id" :value="cap.id">{{cap.name}}{{cap.admin_only?'（管理员）':''}}</option></select><button class="secondary" :disabled="busy || !dataChoice || !enabled">查询所选</button></form>
      <form class="assistant-composer" @submit.prevent="send()"><label class="assistant-input-label" for="assistant-input">向助手提问</label><textarea id="assistant-input" ref="input" v-model="text" rows="2" maxlength="2000" :disabled="!enabled" placeholder="问一问当前数据，Enter 发送" @keydown="submitKey"></textarea><button v-if="busy" type="button" class="secondary" @click="cancel">停止生成</button><button v-else class="primary" :disabled="!text.trim() || !enabled">发送</button></form>
      <p class="assistant-note">只读分析 · 以数据依据为准 · 对话仅保留在当前页面</p>
    </div>
  </section>
</template>

<style scoped>
.assistant-panel{position:fixed;right:0;top:0;bottom:0;width:400px;z-index:80;background:var(--bg-card);border-left:1px solid var(--border);display:flex;flex-direction:column;box-shadow:-6px 0 24px #171a1d0a;min-height:0}
.assistant-header{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:18px;border-bottom:1px solid var(--border);flex-shrink:0}
.assistant-header h2{font-size:17px}.assistant-header p{margin-top:5px;overflow-wrap:anywhere}.assistant-header-actions{display:flex;align-items:center;gap:10px;flex-shrink:0}.assistant-close{font-size:22px;padding:4px 12px;min-height:40px}
.assistant-scope{background:var(--bg-subtle);color:var(--text-secondary);padding:10px 18px;font-size:12px;overflow-wrap:anywhere;flex-shrink:0}
.assistant-log{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:20px 18px;scrollbar-gutter:stable}
.assistant-welcome{padding:16px 0}.assistant-welcome p{margin-top:12px;color:var(--text-secondary);line-height:1.7}.assistant-exchange+.assistant-exchange{margin-top:26px}
.assistant-question{background:var(--bg-subtle);padding:12px;border-radius:12px 12px 3px 12px;max-width:94%;width:fit-content;margin:0 0 20px auto;overflow-wrap:anywhere;white-space:pre-wrap}
.assistant-answer-label{color:var(--text-secondary);font-size:12px;margin-bottom:8px}.assistant-answer-text{white-space:pre-wrap;line-height:1.85;overflow-wrap:anywhere}.assistant-answer .notice{font-size:12px;padding:10px;margin-bottom:12px}
.assistant-event-check{margin-bottom:14px;padding:12px;background:var(--bg-subtle);border-radius:6px}.assistant-event-check strong{color:var(--action)}.assistant-event-check p{white-space:pre-wrap;line-height:1.8;margin-top:10px;font-size:13px}
.assistant-capability-picker{display:flex;align-items:center;gap:8px;margin-bottom:12px}.assistant-capability-picker label{font-size:12px;flex-shrink:0}.assistant-capability-picker select{min-width:0;width:100%;flex:1;font-size:12px;padding:8px}.assistant-capability-picker button{font-size:12px;flex-shrink:0;padding:9px}.assistant-group-actions{display:flex;gap:12px;margin-top:6px}.assistant-group-actions button{color:var(--action);min-height:36px}
.assistant-groups{margin-top:16px;min-width:0}.assistant-group-filter{margin-bottom:12px}.assistant-group-filter>div{display:flex;gap:8px;margin-top:6px}.assistant-group-filter input{min-width:0;flex:1;width:100%}.assistant-group-filter button{flex-shrink:0;padding:8px 12px}.assistant-groups ol{padding-left:22px;margin:10px 0}.assistant-groups li{padding:10px 0;border-bottom:1px solid var(--border);overflow-wrap:anywhere}.assistant-groups li strong{font-size:14px}.assistant-groups li p,.assistant-groups li small{font-size:12px;color:var(--text-secondary);margin-top:5px;display:block}.assistant-group-pages{display:flex;align-items:center;justify-content:space-between;gap:6px;flex-wrap:wrap;margin-top:12px;font-size:12px}.assistant-group-pages button{font-size:12px;padding:8px}
.assistant-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:16px 0}.assistant-cards>div{border:1px solid var(--border);padding:12px;border-radius:8px;display:flex;flex-direction:column;gap:6px}.assistant-cards span,.assistant-cards small{font-size:12px;color:var(--text-secondary)}.assistant-cards strong{font-size:21px;font-weight:600;font-variant-numeric:tabular-nums}
.assistant-citation{border-top:1px solid var(--border);padding-top:12px;margin-top:14px;color:var(--text-secondary);font-size:12px;line-height:1.8}.assistant-sources{margin-top:14px}.assistant-sources button{display:block;text-align:left;margin-top:6px;color:var(--action);min-height:36px;overflow-wrap:anywhere}.assistant-rank{margin-top:16px}.assistant-rank p{display:flex;justify-content:space-between;gap:10px;margin-top:8px;overflow-wrap:anywhere}
.assistant-bottom{padding:12px 16px max(12px,env(safe-area-inset-bottom));border-top:1px solid var(--border);flex-shrink:0}.assistant-shortcuts{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}.assistant-shortcuts button{font-size:12px;padding:7px 9px;white-space:normal}.assistant-composer{display:flex;gap:8px;align-items:flex-end;position:relative}.assistant-composer textarea{font:inherit;resize:none;border:1px solid var(--border);border-radius:6px;background:var(--bg-page);color:var(--text-primary);padding:10px;min-width:0;flex:1;max-height:130px}.assistant-composer textarea:focus-visible{outline:3px solid var(--focus)}.assistant-composer button{flex-shrink:0;min-height:42px;padding:10px}.assistant-input-label{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}.assistant-note{color:var(--text-secondary);font-size:11px;margin-top:8px}.assistant-progress{color:var(--text-secondary);line-height:1.8}.assistant-backdrop{display:none}
@media(max-width:1100px){.assistant-backdrop{display:block;position:fixed;inset:0;background:#171a1d55;z-index:79}.assistant-panel{width:min(440px,100%)}}
@media(max-width:760px){.assistant-panel{width:100%;height:var(--assistant-height,100dvh);top:var(--assistant-top,0px);bottom:auto;border-left:0;z-index:110}.assistant-header{padding:12px 16px}.assistant-composer textarea{font-size:16px}.assistant-shortcuts button{min-height:40px}.assistant-bottom{padding-bottom:max(12px,env(safe-area-inset-bottom))}.assistant-note{font-size:12px}}
@media(max-height:580px){.assistant-shortcuts,.assistant-capability-picker{display:none}.assistant-header{padding-block:8px}.assistant-scope{padding-block:5px}.assistant-note{display:none}}
</style>
