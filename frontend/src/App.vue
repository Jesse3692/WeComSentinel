<script setup lang="ts">
import { ref, reactive, watch, provide, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { api, setCSRF, invalidateSession, sessionGeneration } from './api'
import Avatar from './Avatar.vue'
import NavIcon from './NavIcon.vue'
import { avatarRetryKey } from './avatarRetry'
import { eventColor, chartColors, themeColor } from './theme/chart'
import { hasMatchedMembers, memberStatus, memberNames, memberReason, groupEventLabel, groupClassificationNote, groupEvidenceText } from './groupEvidence'
echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

type AnyRow = Record<string, any>
const config = ref<AnyRow>({}), me = ref<AnyRow | null>(null), tab = ref('dashboard')
const avatarRetry = ref(0)
provide(avatarRetryKey, avatarRetry)
const logoutPending = ref(false), pageError = ref(''), effectiveRange = ref<string[]>([])
const employeeKeyword = ref(''), employeeOffset = ref(0), employeeMore = ref(false)
let employeeVersion = 0
const password = ref(''), demoRole = ref('admin'), error = ref(''), busy = ref(false), loading = ref(false)
const tenant = ref('all'), startDate = ref(''), endDate = ref(''), employee = ref(''), addWay = ref(''), groupSearch = ref('')
const groupType = ref('group_left'), dashboard = ref<AnyRow | null>(null), records = ref<AnyRow[]>([])
const nextCursor = ref<string | null>(null), totals = ref({records: 0, quantity: 0}), employees = ref<AnyRow[]>([])
const detail = ref<AnyRow | null>(null), adminTenants = ref<AnyRow[]>([]), users = ref<AnyRow[]>([]), jobs = ref<AnyRow[]>([])
const tenantForm = reactive({name: '', corp_id: '', credential_ref: '', enabled: true})
const userForm = reactive({name: '', ding_corp_id: '', ding_user_id: ''})
const chartEl = ref<HTMLElement | null>(null), sentinel = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | undefined, resize: ResizeObserver | undefined, observer: IntersectionObserver | undefined
let version = 0, searchTimer: ReturnType<typeof setTimeout>, touchStart = 0, refreshTimer: ReturnType<typeof setInterval>
const labels: Record<string, string> = {contact_added:'新增联系人', contact_deleted:'删除联系人', group_left:'主动退群', group_removed:'被移出群', group_change_unknown:'其他 / 待核实'}
const nav = [{id:'dashboard', name:'汇总'}, {id:'added', name:'新增'}, {id:'deleted', name:'删除'}, {id:'groups', name:'群变更'}, {id:'me', name:'我的'}]
const colors = chartColors()
const types = ['contact_added','contact_deleted','group_left','group_removed']
const addWayLabel = (value: string | null) => ({'1':'扫描二维码','2':'搜索手机号','3':'名片分享','4':'群聊添加','5':'手机通讯录','6':'微信联系人'}[value || ''] || (value?'其他方式':'方式未知'))
const dateText = (value: number) => new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(value)
const tenantName = (id: number) => me.value?.tenants.find((t: AnyRow) => t.id === id)?.name || '未授权主体'
const isGroupEvent = (row: AnyRow) => ['group_left', 'group_removed', 'group_change_unknown'].includes(row.event_type)
const groupName = (row: AnyRow) => row.snapshot.group_name?.trim() || '未命名或群名称未提供'
function eventLabel(row: AnyRow) {
  if (isGroupEvent(row)) return groupEventLabel(row)
  return labels[row.event_type]
}
function query(extra: Record<string,string> = {}) {
  const p = new URLSearchParams({tenant_id: tenant.value, ...extra})
  if(startDate.value && !extra.start_date) p.set('start_date',startDate.value)
  if(endDate.value && !extra.end_date) p.set('end_date',endDate.value)
  return '?' + p.toString()
}
async function loadMe(restore = true, expectedVersion?: number) {
  const data = await api('/me')
  if(expectedVersion !== undefined && expectedVersion !== version) return
  me.value = data; setCSRF(data.csrf_token)
  const desired = restore ? (data.last_tenant_id ? String(data.last_tenant_id) : 'all') : tenant.value
  tenant.value = desired === 'all' || data.tenants.some((t: AnyRow)=>String(t.id)===desired) ? desired : 'all'
}
function clearSession() {
  invalidateSession(); version++; employeeVersion++; clearTimeout(searchTimer)
  me.value=null; detail.value=null; dashboard.value=null; records.value=[]; employees.value=[]
  nextCursor.value=null; totals.value={records:0,quantity:0}; effectiveRange.value=[]
  adminTenants.value=[]; users.value=[]; jobs.value=[]; loading.value=false; busy.value=false
  Object.assign(tenantForm,{name:'',corp_id:'',credential_ref:'',enabled:true})
  Object.assign(userForm,{name:'',ding_corp_id:'',ding_user_id:''})
  employee.value='';employeeKeyword.value='';addWay.value='';groupSearch.value='';startDate.value='';endDate.value='';password.value=''
  pageError.value=''; employeeMore.value=false;employeeOffset.value=0; chart?.dispose(); resize?.disconnect(); observer?.disconnect()
  if(document.body.style.overflow==='hidden') document.body.style.overflow=previousOverflow
}
async function login(demo = false) {
  if(busy.value || logoutPending.value) return
  let generation = sessionGeneration()
  busy.value = true; error.value = ''
  try {
    if(demo) await api('/auth/demo', {password: password.value, role: demoRole.value}, 'POST',false)
    else {
      const dd = await import('dingtalk-jsapi')
      const code = await new Promise<string>((resolve,reject) => {
        const timeout = setTimeout(() => reject(new Error('请在钉钉工作台打开应用，或检查应用配置')),12000)
        dd.ready(() => {
          dd.runtime.permission.requestAuthCode({corpId:config.value.ding_corp_id})
            .then(res=>{clearTimeout(timeout);resolve(res.code)})
            .catch(()=>{clearTimeout(timeout);reject(new Error('钉钉免登失败，请检查应用权限与可见范围'))})
        })
      })
      if(generation!==sessionGeneration()) return
      await api('/auth/dingtalk',{code},'POST',false)
    }
    if(generation!==sessionGeneration()) return
    invalidateSession(); generation=sessionGeneration()
    tab.value='dashboard'; employee.value=''; addWay.value=''; groupSearch.value=''
    sessionStorage.removeItem('logged-out')
    await loadMe(); password.value = ''; await refresh()
  } catch(e: any) {if(generation===sessionGeneration()) error.value = e.message} finally {if(generation===sessionGeneration()) busy.value = false}
}
async function logout() {
  if(logoutPending.value && busy.value) return
  sessionStorage.setItem('logged-out','1')
  // Retain CSRF for a failed logout retry; invalidate every other request first.
  const token = me.value?.csrf_token || logoutToken
  clearSession(); logoutToken=token; setCSRF(token || ''); logoutPending.value=true
  busy.value=true; error.value=''
  try {
    await api('/auth/logout',{},'POST',false)
    logoutPending.value=false; logoutToken=''; setCSRF('')
  } catch(e:any) {
    if(e.status===401) {logoutPending.value=false;logoutToken='';setCSRF('')}
    else error.value='退出请求未完成，服务器会话可能仍有效，请重试退出。'
  } finally {busy.value=false}
}
let logoutToken = ''
const chartBottom = () => window.innerWidth<=760?88:58
async function drawChart() {
  await nextTick()
  if(!chartEl.value || !dashboard.value || tab.value !== 'dashboard') return
  if (!chart || chart.isDisposed() || chart.getDom() !== chartEl.value) {
    chart?.dispose(); resize?.disconnect(); chart = echarts.init(chartEl.value)
    resize = new ResizeObserver(()=>{
      if(!chart || chart.isDisposed()) return
      chart.setOption({grid:{bottom:chartBottom()}})
      chart.resize()
    }); resize.observe(chartEl.value)
  }
  const data = dashboard.value.trend
  chart.setOption({color:colors, tooltip:{trigger:'axis'}, legend:{bottom:0,icon:'circle',textStyle:{color:themeColor('--text-secondary')}},
    grid:{left:38,right:18,top:20,bottom:chartBottom()}, xAxis:{type:'category',boundaryGap:false,data:data.map((d:AnyRow)=>d.date),axisLine:{lineStyle:{color:themeColor('--border')}},axisTick:{show:false},axisLabel:{color:themeColor('--text-secondary'),formatter:(value:string)=>value.slice(5)}},
    yAxis:{type:'value',minInterval:1,splitLine:{lineStyle:{color:themeColor('--border')}},axisLabel:{color:themeColor('--text-secondary')}},
    series:types.map((t,i)=>({name:labels[t],type:'line',smooth:true,showSymbol:false,lineStyle:{width:2.5},areaStyle:i===0?{opacity:.05}:undefined,data:data.map((d:AnyRow)=>d[t])}))})
}
async function fetchRecords(more = false, currentVersion = version) {
  const eventType = tab.value==='added'?'contact_added':tab.value==='deleted'?'contact_deleted':groupType.value
  const params: Record<string,string> = {event_type:eventType}
  if(employee.value && tab.value!=='groups') {
    const [employeeTenant, employeeId] = JSON.parse(employee.value)
    params.employee_id=employeeId; params.employee_tenant_id=String(employeeTenant)
  }
  if(addWay.value && tab.value==='added') params.add_way=addWay.value
  if(groupSearch.value && tab.value==='groups') params.group_keyword=groupSearch.value
  if(more && nextCursor.value) {
    params.cursor=nextCursor.value
    if(effectiveRange.value.length===2) {params.start_date=effectiveRange.value[0];params.end_date=effectiveRange.value[1]}
  }
  const data = await api('/events'+query(params))
  if(currentVersion !== version) return
  records.value=more?[...records.value,...data.items]:data.items
  effectiveRange.value=data.range || []; nextCursor.value=data.next_cursor; totals.value={records:data.total_records,quantity:data.total_quantity}
}
function refreshWithAvatars() {
  avatarRetry.value++
  void refresh()
}
async function refresh(reset = false) {
  if(!me.value) return
  const currentVersion=++version; loading.value=true;error.value='';pageError.value=''; if(reset) detail.value=null
  try {
    if(tab.value==='dashboard') {
      if(reset) dashboard.value=null
      const data=await api('/dashboard'+query()); if(currentVersion!==version)return
      dashboard.value=data; await drawChart()
    } else if(['added','deleted','groups'].includes(tab.value)) {
      if(reset) {records.value=[];nextCursor.value=null;totals.value={records:0,quantity:0};effectiveRange.value=[]}
      await fetchRecords(false,currentVersion)
      if(currentVersion===version && tab.value!=='groups') void loadEmployees()
    } else if(tab.value==='me') {
      await loadMe(false,currentVersion)
    } else if(tab.value==='admin') {
      const data=await Promise.all([api('/admin/tenants'),api('/admin/users'),api('/admin/jobs')])
      if(currentVersion===version)[adminTenants.value,users.value,jobs.value]=data
    }
  } catch(e:any){if(currentVersion===version)error.value=(dashboard.value && tab.value==='dashboard' || records.value.length && ['added','deleted','groups'].includes(tab.value) ? '更新失败，当前保留上次数据：' : '加载失败：')+e.message}
  finally{if(currentVersion===version)loading.value=false}
}
async function loadMore() {
  if(loading.value || !nextCursor.value)return
  const v=version;loading.value=true;pageError.value=''
  try{await fetchRecords(true,v)}catch(e:any){if(v===version)pageError.value='加载更多失败：'+e.message}finally{if(v===version)loading.value=false}
}
async function mutation(path:string, body:unknown, method='POST') {
  const generation=sessionGeneration()
  busy.value=true;error.value=''
  try{await api(path,body,method);await loadMe(false);await refresh();return true}catch(e:any){if(generation===sessionGeneration())error.value=e.message;return false}finally{if(generation===sessionGeneration())busy.value=false}
}
async function createTenant(){if(await mutation('/admin/tenants',tenantForm))Object.assign(tenantForm,{name:'',corp_id:'',credential_ref:'',enabled:true})}
async function createUser(){if(await mutation('/admin/users',userForm))Object.assign(userForm,{name:'',ding_corp_id:'',ding_user_id:''})}
async function saveTenant(t:AnyRow){await mutation('/admin/tenants/'+t.id,{name:t.name,credential_ref:t.credential_ref,enabled:t.enabled},'PATCH')}
async function saveGrants(u:AnyRow){await mutation('/admin/users/'+u.id+'/tenants',{tenant_ids:u.tenant_ids},'PUT')}
function cardClick(type:string){if(type==='contact_added')tab.value='added';else if(type==='contact_deleted')tab.value='deleted';else{groupType.value=type;tab.value='groups'}}
function pullStart(e:TouchEvent){if(!detail.value && window.scrollY<=0)touchStart=e.touches[0].clientY;else touchStart=0}
function pullEnd(e:TouchEvent){if(!detail.value && touchStart && e.changedTouches[0].clientY-touchStart>90 && !loading.value)refreshWithAvatars();touchStart=0}
function expired(){
  if(!me.value) return
  clearSession(); error.value='登录已过期，请重新登录'
  if(!sessionStorage.getItem('logged-out') && !logoutPending.value && config.value.ding_configured && /DingTalk/i.test(navigator.userAgent)) void login()
}
watch(tenant,()=>{employee.value='';employees.value=[];employeeKeyword.value='';employeeVersion++}, {flush:'sync'})
watch([tenant,startDate,endDate,tab,employee,addWay,groupType],()=>{void refresh(true)})
let preferenceQueue: Promise<void> = Promise.resolve()
watch(tenant,()=>{
  if(!me.value) return
  const selected=tenant.value, generation=sessionGeneration()
  preferenceQueue=preferenceQueue.catch(()=>{}).then(async()=>{
    if(generation!==sessionGeneration() || !me.value) return
    try {await api('/me/preferences',{last_tenant_id:selected==='all'?null:Number(selected)},'PATCH')}
    catch(e:any){if(generation===sessionGeneration() && selected===tenant.value)error.value='主体偏好保存失败：'+e.message}
  })
})
async function loadEmployees(more = false) {
  if(!me.value || logoutPending.value || !['added','deleted'].includes(tab.value)) return
  const pageVersion=version
  const current=++employeeVersion, generation=sessionGeneration(), selected=tenant.value
  const offset=more?employeeOffset.value:0
  try {
    const data=await api('/employees'+query({keyword:employeeKeyword.value,offset:String(offset),limit:'100'}))
    if(current!==employeeVersion || generation!==sessionGeneration() || selected!==tenant.value || pageVersion!==version) return
    const retained=employees.value.filter(e=>JSON.stringify([e.tenant_id,e.id])===employee.value)
    const rows=more?[...employees.value,...data]:[...retained,...data]
    employees.value=Array.from(new Map(rows.map(e=>[JSON.stringify([e.tenant_id,e.id]),e])).values())
    employeeOffset.value=offset+data.length;employeeMore.value=data.length===100
  } catch(e:any){if(current===employeeVersion && generation===sessionGeneration() && pageVersion===version)error.value=e.message}
}
watch(employeeKeyword,()=>{void loadEmployees()})
watch(groupSearch,()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>void refresh(true),300)})
watch(sentinel,(el)=>{observer?.disconnect();if(el){observer=new IntersectionObserver(entries=>{if(entries[0].isIntersecting && !pageError.value)void loadMore()});observer.observe(el)}})
// An intersection can arrive while another page request is loading. Recheck
// after completion so staying at the bottom never requires an extra gesture.
watch([loading,nextCursor], async()=>{
  if(loading.value || !nextCursor.value || error.value || pageError.value || !['added','deleted','groups'].includes(tab.value)) return
  await nextTick()
  const box=sentinel.value?.getBoundingClientRect()
  if(box && box.top<window.innerHeight && box.bottom>0) void loadMore()
})
const dialogEl = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null
let previousOverflow = ''
watch(detail, async (value, oldValue)=>{
  if(value && !oldValue) {
    previousFocus = document.activeElement as HTMLElement
    previousOverflow = document.body.style.overflow
    document.body.style.overflow='hidden'
    await nextTick(); dialogEl.value?.focus()
  } else if(!value && oldValue) {
    document.body.style.overflow=previousOverflow
    await nextTick(); previousFocus?.focus()
  }
})
function dialogKey(e: KeyboardEvent) {
  if(e.key==='Escape') {e.preventDefault();detail.value=null}
  if(e.key!=='Tab') return
  const nodes=dialogEl.value?.querySelectorAll<HTMLElement>('button, input, select, a[href], [tabindex="0"]')
  if(!nodes?.length) {e.preventDefault();return}
  const first=nodes[0], last=nodes[nodes.length-1]
  if(e.shiftKey && (document.activeElement===first || document.activeElement===dialogEl.value)) {e.preventDefault();last.focus()}
  else if(!e.shiftKey && (document.activeElement===last || document.activeElement===dialogEl.value)) {e.preventDefault();first.focus()}
}
onMounted(async()=>{
  window.addEventListener('session-expired',expired)
  try{config.value=await api('/config');try{if(!sessionStorage.getItem('logged-out')){await loadMe();await refresh()}}catch{if(config.value.ding_configured && /DingTalk/i.test(navigator.userAgent) && !sessionStorage.getItem('logged-out'))await login()}}
  catch(e:any){error.value=e.message}
  refreshTimer=setInterval(()=>{if(me.value && tab.value==='dashboard' && !loading.value && !document.hidden)void refresh()},30000)
})
onBeforeUnmount(()=>{chart?.dispose();resize?.disconnect();observer?.disconnect();clearTimeout(searchTimer);clearInterval(refreshTimer);window.removeEventListener('session-expired',expired)})
</script>

<template>
  <div v-if="!me" class="login-shell">
    <section class="login-card">
      <div class="brandmark">◫</div><p class="eyebrow">CUSTOMER RELATIONSHIP MONITOR</p>
      <h1>每一次关系变化，<br>都值得被看见。</h1><p class="muted">连接企业微信，在一个工作台掌握客户与团队的动态。</p>
      <div v-if="error" role="alert" class="error">{{error}}</div>
      <button v-if="logoutPending" class="secondary wide" :disabled="busy" @click="logout">重试退出</button><button class="primary wide" :disabled="busy || logoutPending || !config.ding_configured" @click="login()">{{busy?'正在验证身份…':'通过钉钉进入工作台 →'}}</button>
      <p v-if="!config.ding_configured" class="hint">钉钉应用尚未配置，接入步骤见 docs/配置指南.md。</p>
      <form v-if="config.demo_enabled" class="demo-login" @submit.prevent="login(true)">
        <span class="badge">本地演示 · 模拟数据</span><p>输入 .env 中的 DEMO_PASSWORD。普通用户仅有总部权限。</p>
        <label>演示角色<select v-model="demoRole"><option value="admin">管理员 · 全部主体</option><option value="user">主管 · 单个主体</option></select></label>
        <label>演示密码<input v-model="password" type="password" autocomplete="current-password" required placeholder="本地演示密码"></label>
        <button class="secondary wide" :disabled="busy || logoutPending">进入演示</button>
      </form>
      <p class="login-footer">企微数据接入 · 钉钉工作台 · 主体权限隔离</p>
    </section>
  </div>
  <div v-else class="app-shell" @touchstart.passive="pullStart" @touchend.passive="pullEnd">
    <aside class="sidebar">
      <div class="brand"><span class="brandmark">◫</span><div>关系观察台<small>CLIENT INSIGHT</small></div></div>
      <p class="nav-label">工作空间</p>
      <nav><button v-for="n in nav" :key="n.id" :class="{active:tab===n.id}" @click="tab=n.id"><NavIcon :name="n.id" />{{n.name}}<i v-if="tab===n.id">●</i></button></nav>
      <button v-if="me.role==='admin'" class="admin-nav" :class="{active:tab==='admin'}" @click="tab='admin'">⚙ 主体与权限管理</button>
      <div class="sidebar-bottom"><Avatar :name="me.name" :url="me.avatar_url" /><div><span class="sidebar-user-name" :title="me.name">{{me.name}}</span><small>{{me.role==='admin'?'管理员':'授权用户'}}</small></div><button class="text-btn" @click="logout" title="退出登录">↗</button></div>
    </aside>
    <main>
      <header class="topbar"><span>工作台 <span class="slash">/</span> {{tab==='admin'?'配置管理':nav.find(n=>n.id===tab)?.name}}</span><span class="environment"><span class="dot"></span>{{config.demo_enabled?'本地演示环境':'企业工作空间'}}</span></header>
      <section class="page-heading"><div><p class="eyebrow">{{tab==='dashboard'?'OVERVIEW':'WORKSPACE'}}</p><h1>{{tab==='dashboard'?'客户关系汇总':tab==='admin'?'主体与权限管理':tab==='me'?'个人中心':tab==='groups'?'群成员变更':tab==='added'?'新增联系人':'删除联系人'}}</h1><p class="muted">{{tab==='dashboard'?'看见增长，也看见每一次流失。':tab==='admin'?'管理接入配置、访问范围与事件处理任务。':'所有记录按主体授权展示，时间统一为北京时间。'}}</p></div><button class="secondary" :disabled="loading" @click="refreshWithAvatars()">↻ {{loading?'更新中…':'刷新数据'}}</button></section>
      <div v-if="config.demo_enabled" class="demo-banner">演示模式：页面数据由本地种子生成，不代表真实企业数据或平台联调结果。</div>
      <p v-if="loading" class="loading-status" role="status">正在更新数据，网络较慢时请稍候…</p><div v-if="error" class="error" role="alert">{{error}} <button class="text-btn" @click="refreshWithAvatars()">重试</button></div>
      <section v-if="!['me','admin'].includes(tab)" class="filters">
        <label>当前主体<select v-model="tenant"><option value="all">全部授权主体</option><option v-for="t in me.tenants" :key="t.id" :value="String(t.id)">{{t.name}}</option></select></label>
        <template v-if="tab==='dashboard'"><label>开始日期<input v-model="startDate" type="date" aria-label="开始日期"></label><span class="date-sep">—</span><label>结束日期<input v-model="endDate" type="date" aria-label="结束日期"></label>
        <button class="text-btn" @click="startDate='';endDate=''">恢复默认</button><span class="filter-note">默认近 7 天 · 趋势近 30 天</span></template><div v-else class="inherited-range">统计区间：{{effectiveRange.length===2 ? effectiveRange.join(' 至 ') : (loading ? '正在读取' : '未取得有效日期')}} <button class="text-btn" @click="tab='dashboard'">调整日期筛选 →</button></div>
      </section>
      <template v-if="tab==='dashboard'">
        <div v-if="!dashboard && loading" class="empty">正在加载汇总…</div>
        <template v-if="dashboard">
          <p class="range-summary">指标与排名区间：{{dashboard.metrics_range[0]}} 至 {{dashboard.metrics_range[1]}}</p><div class="metrics"><button v-for="(card,i) in dashboard.cards" :key="card.type" class="metric" @click="cardClick(card.type)"><div class="metric-top"><span>{{labels[card.type]}}</span><span class="metric-symbol" :style="{color:eventColor(card.type)}">{{['↗','↘','↪','⊖'][Number(i)]}}</span></div><strong>{{card.value.toLocaleString()}}<small>人次</small></strong><div class="metric-footer"><span :class="card.delta>0?'up':card.delta<0?'down':'neutral'">{{card.delta>0?'↑ +':card.delta<0?'↓ ':''}}{{card.delta}}</span><span>较上一周期</span><span class="metric-arrow">↗</span></div></button></div>
          <div v-if="dashboard.unresolved_count || dashboard.pending_jobs" class="notice">有 {{dashboard.unresolved_count}} 人次属于其他群成员变更或仍待核实，未计入外部客户流失指标，{{dashboard.pending_jobs}} 个任务尚未完成。详情和指标可能继续补全。</div>
          <section class="panel trend-panel"><div class="panel-title"><div><h2>关系变化趋势</h2><p>按日查看四类事件，点击图例切换显示</p></div><span class="badge">{{dashboard.trend.length}} 天</span></div><div ref="chartEl" class="chart" aria-label="四类事件趋势图"></div><p class="chart-foot">统计区间 {{dashboard.trend_range[0]}} 至 {{dashboard.trend_range[1]}} · 截至 {{dateText(dashboard.updated_at)}}</p></section>
          <div class="rankings"><section v-for="(type,i) in ['contact_added','contact_deleted','group_left']" :key="type" class="panel"><div class="panel-title"><h2>{{['新增客户员工','删除关系员工','主动退群群聊'][i]}} <span class="top-label">TOP 5</span></h2><button class="text-btn" @click="cardClick(type)">明细 ↗</button></div><div v-if="!dashboard.tops[type].length" class="empty small">暂无数据</div><div v-for="(r,index) in dashboard.tops[type]" :key="r.tenant_id+':'+r.id" class="rank-row"><span class="rank-number" :class="{first:index===0}">{{String(Number(index)+1).padStart(2,'0')}}</span><div class="rank-name">{{r.name}}<small>{{tenantName(r.tenant_id)}}</small><div class="rank-bar"><i :style="{width:(r.count/dashboard.tops[type][0].count*100)+'%',background:eventColor(type)}"></i></div></div><strong>{{r.count}}<small>人次</small></strong></div></section></div>
        </template>
      </template>
      <template v-else-if="['added','deleted','groups'].includes(tab)">
        <section class="detail-toolbar"><div v-if="tab==='groups'" class="tabs"><button v-for="t in ['group_left','group_removed','group_change_unknown']" :class="{selected:groupType===t}" @click="groupType=t">{{labels[t]}}</button></div><label v-else>员工<input v-model="employeeKeyword" placeholder="搜索员工姓名或ID" aria-label="搜索员工"><select v-model="employee"><option value="">全部员工</option><option v-for="e in employees" :key="e.tenant_id+':'+e.id" :value="JSON.stringify([e.tenant_id,e.id])">{{e.name}} · {{tenantName(e.tenant_id)}}</option></select></label><button v-if="employeeMore && tab!=='groups'" class="text-btn" @click="loadEmployees(true)">更多员工</button><label v-if="tab==='added'">添加方式<select v-model="addWay"><option value="">全部方式</option><option value="1">扫描二维码</option><option value="2">搜索手机号</option><option value="3">名片分享</option><option value="4">群聊添加</option><option value="5">手机通讯录</option><option value="6">微信联系人</option><option value="other">其他</option><option value="unknown">未知</option></select></label><input v-if="tab==='groups'" v-model="groupSearch" placeholder="搜索群名称" aria-label="群名称搜索"><span class="muted">{{totals.records}} 条记录 · {{totals.quantity}} 人次</span></section>
        <p v-if="tab==='groups' && groupType==='group_change_unknown'" class="muted">这里保留内部员工变更，以及客户身份或退出原因证据不足的记录。已识别的外部客户按主动退群、被移出群分类。</p>
        <section class="panel event-list"><div v-if="!records.length && !error" class="empty"><span>◎</span><h3>{{loading?'正在加载…':'暂无数据'}}</h3><p>调整主体或日期范围后再试</p></div><button v-for="e in records" :key="e.id" class="event-row" @click="detail=e"><div v-if="isGroupEvent(e) && hasMatchedMembers(e)" class="member-avatars"><Avatar v-for="m in e.snapshot.member_candidates.slice(0,3)" :key="m.member_id" :name="m.name" :media-id="m.media_id" /><span v-if="e.quantity>3">+{{e.quantity-3}}</span></div><Avatar v-else :name="isGroupEvent(e) ? (e.resolution==='exact' ? e.snapshot.customer_name : '?') : e.snapshot.customer_name" :media-id="!isGroupEvent(e) || e.resolution==='exact' ? e.snapshot.customer_media_id : undefined" :warm="tab==='deleted'" /><div class="event-content"><strong>{{isGroupEvent(e) ? ((e.resolution==='exact' || hasMatchedMembers(e)) ? memberNames(e) : '成员待核实') : memberNames(e)}} <span v-if="e.quantity>1" class="badge">{{e.quantity}} 人次</span></strong><p v-if="e.event_type==='contact_added'">由 {{e.snapshot.employee_name}} 添加 · {{addWayLabel(e.add_way)}}</p><p v-else-if="e.event_type==='contact_deleted'">{{e.deletion_direction==='customer_deleted_employee'?'客户删除了员工':e.deletion_direction==='employee_deleted_customer'?'员工删除了客户':'删除方向未提供'}} · {{e.snapshot.employee_name}}</p><p v-else>{{eventLabel(e)}} · {{groupName(e)}}<span v-if="e.operator_id"> · 操作人 {{e.operator_id}}</span></p><div v-if="!isGroupEvent(e)" class="employee-identity"><Avatar :name="e.snapshot.employee_name" :media-id="e.snapshot.employee_media_id" /><span>员工 · {{e.snapshot.employee_name || e.employee_id || '未提供'}}</span></div><small>{{tenantName(e.tenant_id)}}<span v-if="e.resolution!=='exact'"> · {{memberStatus(e)}}{{hasMatchedMembers(e) ? '（系统比对推断）' : ''}}</span><span v-if="e.enrichment_status!=='complete'"> · 资料待补全</span><span v-if="e.channel_state"> · 渠道 {{e.channel_state}}</span></small></div><time>{{dateText(e.occurred_at)}}</time><span class="muted">›</span></button></section>
        <p v-if="pageError" class="error" role="alert">{{pageError}}</p><div ref="sentinel" class="load-more"><button v-if="nextCursor" class="secondary" :disabled="loading" @click="loadMore">{{loading?'加载中…':'加载更多'}}</button><span v-else-if="records.length">已显示全部记录</span></div>
      </template>
      <section v-else-if="tab==='me'" class="panel profile"><Avatar :name="me.name" :url="me.avatar_url" large /><h2>{{me.name}}</h2><span class="badge">{{me.role==='admin'?'管理员':'普通用户'}}</span><h3>可访问主体</h3><div v-for="t in me.tenants" class="profile-tenant">◫ {{t.name}}</div><p v-if="!me.tenants.length" class="muted">暂无授权主体，请联系管理员。</p><button v-if="me.role==='admin'" class="secondary" @click="tab='admin'">管理主体与授权</button><button class="secondary" @click="logout">退出登录</button></section>
      <template v-else-if="tab==='admin'">
        <section class="panel admin-panel"><h2>接入主体</h2><p class="muted">密钥仅保存在服务器 .env；凭据引用例如 ACME 对应 WECOM_ACME_SECRET 等配置。</p><form class="admin-form" @submit.prevent="createTenant"><input v-model="tenantForm.name" placeholder="主体名称" required><input v-model="tenantForm.corp_id" placeholder="企业 CorpID" required><input v-model="tenantForm.credential_ref" placeholder="凭据引用（大写）" pattern="[A-Z][A-Z0-9_]{0,40}" required><button class="primary" :disabled="busy">新增主体</button></form><div v-for="t in adminTenants" :key="t.id" class="admin-item"><div class="admin-form"><input v-model="t.name" aria-label="主体名称"><input v-model="t.credential_ref" aria-label="凭据引用"><label><input type="checkbox" v-model="t.enabled">启用</label><span class="badge">{{t.credentials_ready?'凭据已配置':'待配置凭据'}}</span><button class="secondary" :disabled="busy" @click="saveTenant(t)">保存</button></div><p>CorpID：{{t.corp_id}}</p><code>{{t.callback_url}}</code></div></section>
        <section class="panel admin-panel"><h2>用户与授权</h2><form class="admin-form" @submit.prevent="createUser"><input v-model="userForm.name" placeholder="用户姓名" required><input v-model="userForm.ding_corp_id" placeholder="钉钉 CorpID" required><input v-model="userForm.ding_user_id" placeholder="钉钉 UserID" required><button class="primary" :disabled="busy">新增用户</button></form><div v-for="u in users" :key="u.id" class="admin-item"><strong>{{u.name}}</strong><span class="muted"> · {{u.ding_user_id}}</span><p v-if="u.role==='admin'">管理员可访问所有启用主体</p><template v-else><div class="grant-options"><label v-for="t in adminTenants"><input type="checkbox" :value="t.id" v-model="u.tenant_ids">{{t.name}}</label></div><button class="secondary" :disabled="busy" @click="saveGrants(u)">保存授权</button></template></div></section>
        <section class="panel admin-panel"><h2>最近处理任务</h2><p class="muted">最多显示最近 100 条。失败任务在修复配置后可重新排队。</p><div v-if="!jobs.length" class="empty small">暂无处理任务</div><div v-for="j in jobs" class="job-row"><span>#{{j.id}} · 主体 {{j.tenant_id}}</span><span class="badge">{{j.status}}</span><span>{{j.attempts}} 次尝试</span><code>{{j.last_error}}</code><button v-if="['failed','retry'].includes(j.status)" class="secondary" :disabled="busy" @click="mutation('/admin/jobs/'+j.id+'/retry',{})">重试</button></div></section>
      </template>
      <footer class="page-footer">关系观察台 <span>数据按北京时间统计 · 每 30 秒更新汇总</span></footer>
    </main>
    <nav class="mobile-nav"><button v-for="n in nav" :key="n.id" :class="{active:tab===n.id}" @click="tab=n.id"><NavIcon :name="n.id" />{{n.name}}</button></nav>
    <div v-if="detail" class="modal-backdrop" @click.self="detail=null">
      <section ref="dialogEl" class="modal" tabindex="-1" @keydown="dialogKey" role="dialog" aria-modal="true" aria-label="事件详情">
        <button class="close" @click="detail=null" aria-label="关闭">×</button>
        <p class="eyebrow">EVENT DETAIL</p><h2>{{eventLabel(detail)}}</h2>
        <dl>
          <dt>所属主体</dt><dd>{{tenantName(detail.tenant_id)}}</dd>
          <dt>发生时间</dt><dd>{{dateText(detail.occurred_at)}}</dd>
          <template v-if="isGroupEvent(detail)">
            <dt>{{detail.snapshot.group_name_source==='current_profile'?'群名称（当前）':'群名称'}}</dt><dd>{{groupName(detail)}}</dd>
            <dt>变更类型</dt><dd>{{eventLabel(detail)}}</dd>
            <dt>{{detail.event_type==='group_change_unknown'?'本条涉及人数':'本条客户人数'}}</dt><dd>{{detail.quantity}} 人次</dd>
            <dt v-if="detail.snapshot.reported_quantity && detail.snapshot.reported_quantity!==detail.quantity">同次平台报告人数</dt><dd v-if="detail.snapshot.reported_quantity && detail.snapshot.reported_quantity!==detail.quantity">{{detail.snapshot.reported_quantity}} 人次（含本条之外的成员）</dd>
            <dt>涉及成员</dt><dd>{{memberNames(detail)}}</dd>
            <dt>识别依据</dt><dd>{{groupEvidenceText(detail)}}</dd>
            <dt v-if="detail.snapshot.group_owner_id">群主（非操作人）</dt><dd v-if="detail.snapshot.group_owner_id">{{detail.snapshot.group_owner_name || detail.snapshot.group_owner_id}}</dd>
            <dt>操作人</dt><dd>{{detail.operator_id || '平台未提供'}}</dd>
          </template>
          <template v-else>
            <dt>客户</dt><dd>{{memberNames(detail)}}</dd>
            <dt>员工</dt><dd>{{detail.snapshot.employee_name || detail.employee_id || '员工标识未提供'}}</dd>
            <dt>操作人</dt><dd>{{detail.operator_id || '未提供'}}</dd>
            <dt>客户类型</dt><dd>{{detail.snapshot.customer_type===1?'微信':detail.snapshot.customer_type===2?'企微':'未知'}}</dd>
            <dt>性别</dt><dd>{{detail.snapshot.gender===1?'男':detail.snapshot.gender===2?'女':'未知'}}</dd>
            <dt>所属企业</dt><dd>{{detail.snapshot.company_name || '未知'}}</dd>
            <dt>添加方式编码</dt><dd>{{detail.add_way || '—'}}</dd>
            <dt>渠道参数</dt><dd>{{detail.channel_state || '—'}}</dd>
            <dt>人数</dt><dd>{{detail.quantity}}</dd>
          </template>
          <dt>数据状态</dt><dd>{{memberStatus(detail)}} / {{detail.enrichment_status==='complete'?'资料齐全':'资料待补全'}}</dd>
        </dl>
        <template v-if="detail.snapshot.member_candidates?.length">
          <h3>{{hasMatchedMembers(detail)?'成员资料':'待核实的成员线索'}}</h3>
          <dl v-for="(member,index) in detail.snapshot.member_candidates" :key="index">
            <dt>成员</dt><dd><Avatar v-if="hasMatchedMembers(detail)" :name="member.name" :media-id="member.media_id" />{{member.name || member.member_id || '姓名待补全'}}</dd>
            <dt>成员类型</dt><dd>{{member.member_type===1?'企业内部员工':member.member_type===2?'外部联系人':'未知'}}</dd>
            <dt v-if="member.customer_type">客户类型</dt><dd v-if="member.customer_type">{{member.customer_type===1?'微信':member.customer_type===2?'企微':'未知'}}</dd>
            <dt v-if="member.gender===1 || member.gender===2">性别</dt><dd v-if="member.gender===1 || member.gender===2">{{member.gender===1?'男':'女'}}</dd>
            <dt v-if="member.company_name">所属企业</dt><dd v-if="member.company_name">{{member.company_name}}</dd>
          </dl>
        </template>
        <p v-else-if="detail.event_type==='group_change_unknown'" class="muted">{{memberReason(detail)}}</p>
        <p v-if="isGroupEvent(detail)" :class="detail.event_type==='group_change_unknown' && !hasMatchedMembers(detail)?'notice':'muted'">{{groupClassificationNote(detail)}}</p>
        <p v-else-if="detail.snapshot.note" class="notice">{{detail.snapshot.note}}</p>
        <p v-if="detail.snapshot.demo" class="notice">此条为模拟演示数据。</p>
      </section>
    </div>
  </div>
</template>
