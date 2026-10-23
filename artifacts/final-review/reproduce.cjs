const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const types=['contact_added','contact_deleted','group_left','group_removed'];
const tenants=[{id:1,name:'总部'},{id:2,name:'华东分部'}];
const event={id:1,tenant_id:1,event_type:'group_left',quantity:2,resolution:'exact',enrichment_status:'complete',occurred_at:Date.now(),snapshot:{customer_name:'张女士（模拟客户）',group_name:'客户服务与产品交流长名称测试群',employee_name:'李顾问',demo:true}};
const dashboard={cards:types.map((type,i)=>({type,value:120-i*23,delta:i%2?-3:5})),trend:Array.from({length:30},(_,i)=>({date:new Date(Date.UTC(2026,8,24+i)).toISOString().slice(0,10),...Object.fromEntries(types.map((t,j)=>[t,(i*7+j*3)%20]))})),tops:Object.fromEntries(types.map(t=>[t,[{tenant_id:1,id:'a',name:'客户服务团队（模拟）',count:23}]])),metrics_range:['2026-10-17','2026-10-23'],trend_range:['2026-09-24','2026-10-23'],unresolved_count:2,pending_jobs:1,updated_at:Date.now()};

let failEvents=false, expireEvents=false, logoutStatus=401, logoutCalls=0, preferenceActive=0, preferencePeak=0;
let mode='',held=false,release;let uncertain=false;let savedTenant=null, renamed=false, paginate=false, delayPage=false, pageStarted=false;
await page.route('**/api/v1/**',async route=>{
 const u=new URL(route.request().url());let data={},status=200;
 if(u.pathname.includes('/media/')) {await route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH1cAAAAASUVORK5CYII=','base64')});return}
 if(u.pathname.endsWith('/config')) data={demo_enabled:true,ding_configured:false};
 else if(u.pathname.endsWith('/me'))data={name:'测试管理员',role:'admin',csrf_token:'local-test',tenants:renamed?[{id:1,name:'新总部'}]:tenants};
 else if(u.pathname.endsWith('/auth/logout')){logoutCalls++;status=logoutStatus}
 else if(u.pathname.endsWith('/dashboard'))data=dashboard;
 else if(u.pathname.endsWith('/events')){
  if(u.searchParams.has('cursor')) {pageStarted=true;if(delayPage)await new Promise(r=>setTimeout(r,500));status=500}
  else if(expireEvents) {await new Promise(r=>setTimeout(r,150));status=401}
  else if(failEvents)status=500;
  else data={items:[{...event,event_type:u.searchParams.get('event_type'),resolution:'inferred',snapshot:{...event.snapshot,member_evidence:uncertain?'snapshot_difference_inconclusive':'snapshot_difference',member_candidates:[{member_id:'a',member_type:2,name:'客户甲',media_id:123},{member_id:'b',member_type:2,name:'客户乙',media_id:124}]}}],total_records:2,total_quantity:2,next_cursor:paginate?'next':null,range:['2025-12-25','2025-12-31']};
 }
 else if(u.pathname.endsWith('/employees')){if(mode==='reset' && !u.searchParams.get('keyword')) {held=true;await new Promise(r=>release=r);status=401} else if(mode==='late-error') {await new Promise(r=>setTimeout(r,500));status=500} else data=[{id:'employee',tenant_id:1,name:'员工'}]}
 else if(u.pathname.endsWith('/me/preferences')){preferenceActive++;preferencePeak=Math.max(preferencePeak,preferenceActive);await new Promise(r=>setTimeout(r,100));savedTenant=route.request().postDataJSON().last_tenant_id;preferenceActive--}
 else if(u.pathname.endsWith('/admin/tenants')){if(route.request().method()==='POST')renamed=true;else data=[]}
 else if(u.pathname.includes('/admin/'))data=[];
 await route.fulfill({status,json:status===200?{data}:{error:{message:'simulated failure'}}});
});

await page.goto('http://localhost:8000');await page.locator('.metric').first().waitFor();
await page.locator('.metric').first().click();await page.locator('.event-row').first().waitFor();await page.getByLabel('搜索员工').fill('abc');await page.waitForTimeout(150);
mode='reset';await page.getByTitle('退出登录').click();await page.locator('.login-shell').waitFor();await page.waitForTimeout(100);assert.equal(held,true);
mode='';await page.getByLabel('演示密码').fill('test-password');await page.getByRole('button',{name:'进入演示',exact:true}).click();await page.locator('.metric').first().waitFor();release();await page.locator('.login-shell').waitFor();
console.log('REPRODUCED: clearing employee search issues a new-generation request after logout; late401 clears re-login');
await page.getByLabel('演示密码').fill('test-password');await page.getByRole('button',{name:'进入演示',exact:true}).click();await page.locator('.metric').first().waitFor();
mode='late-error';await page.locator('.metric').first().click();await page.locator('.event-row').first().waitFor();await page.locator('.sidebar nav button').first().click();await page.locator('.metric').first().waitFor();await page.locator('.error').waitFor();assert.ok((await page.locator('.error').innerText()).includes('simulated failure'));
console.log('REPRODUCED: delayed employee error appears after switching to dashboard');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
