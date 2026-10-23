const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const types=['contact_added','contact_deleted','group_left','group_removed'];
const tenants=[{id:1,name:'总部'},{id:2,name:'华东分部'}];
const event={id:1,tenant_id:1,event_type:'group_left',quantity:2,resolution:'exact',enrichment_status:'complete',occurred_at:Date.now(),snapshot:{customer_name:'张女士（模拟客户）',group_name:'客户服务与产品交流长名称测试群',employee_name:'李顾问',demo:true}};
const dashboard={cards:types.map((type,i)=>({type,value:120-i*23,delta:i%2?-3:5})),trend:Array.from({length:30},(_,i)=>({date:new Date(Date.UTC(2026,8,24+i)).toISOString().slice(0,10),...Object.fromEntries(types.map((t,j)=>[t,(i*7+j*3)%20]))})),tops:Object.fromEntries(types.map(t=>[t,[{tenant_id:1,id:'a',name:'客户服务团队（模拟）',count:23}]])),metrics_range:['2026-10-17','2026-10-23'],trend_range:['2026-09-24','2026-10-23'],unresolved_count:2,pending_jobs:1,updated_at:Date.now()};

let failEvents=false, expireEvents=false, logoutStatus=401, logoutCalls=0, preferenceActive=0, preferencePeak=0;
let uncertain=false;let savedTenant=null, renamed=false, paginate=false, delayPage=false, pageStarted=false;
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
 else if(u.pathname.endsWith('/employees'))data=[{id:'employee',tenant_id:1,name:'员工'}];
 else if(u.pathname.endsWith('/me/preferences')){preferenceActive++;preferencePeak=Math.max(preferencePeak,preferenceActive);await new Promise(r=>setTimeout(r,100));savedTenant=route.request().postDataJSON().last_tenant_id;preferenceActive--}
 else if(u.pathname.endsWith('/admin/tenants')){if(route.request().method()==='POST')renamed=true;else data=[]}
 else if(u.pathname.includes('/admin/'))data=[];
 await route.fulfill({status,json:status===200?{data}:{error:{message:'simulated failure'}}});
});
await page.goto('http://localhost:8000');await page.locator('.metric').first().waitFor();
await page.locator('.filters select').selectOption('1');await page.locator('.filters select').selectOption('2');await page.waitForTimeout(450);
assert.equal(preferencePeak,1);assert.equal(savedTenant,2);
await page.locator('.admin-nav').click();await page.getByPlaceholder('主体名称',{exact:true}).fill('新主体');await page.getByPlaceholder('企业 CorpID').fill('test');await page.getByPlaceholder('凭据引用（大写）').fill('TEST');await page.getByRole('button',{name:'新增主体',exact:true}).click();await page.waitForTimeout(150);
await page.locator('.sidebar nav button').first().click();await page.locator('.metric').first().waitFor();assert.equal(await page.locator('.filters select').inputValue(),'all');assert.ok((await page.locator('.filters select').innerText()).includes('新总部'));
await page.locator('.metric').first().click();await page.locator('.event-row').first().waitFor();assert.ok((await page.locator('.inherited-range').innerText()).includes('2025-12-31'));
await page.locator('.sidebar nav button').nth(3).click();await page.locator('.event-row .member-avatars').waitFor();assert.equal(await page.locator('.event-row .member-avatars .avatar').count(),2);
await page.locator('.member-avatars img').first().waitFor();assert.equal(await page.locator('.member-avatars img').count(),2);
for(const img of await page.locator('.member-avatars img').all()){await img.evaluate(async el=>{await el.decode();if(!el.complete || el.naturalWidth!==1)throw new Error('avatar not decoded')})}
assert.deepEqual(await page.locator('.member-avatars img').evaluateAll(imgs=>imgs.map(i=>new URL(i.src).pathname)),['/api/v1/media/123','/api/v1/media/124']);
for(const width of [375,390,428,900,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)}
await page.setViewportSize({width:1440,height:1000});
await page.locator('.event-row').first().click();assert.equal(await page.getByRole('dialog').locator('.avatar').count(),2);for(const img of await page.getByRole('dialog').locator('.avatar img').all()){await img.evaluate(async el=>{await el.decode();if(!el.complete || el.naturalWidth!==1)throw new Error('detail avatar not decoded')})}await page.getByRole('button',{name:'关闭',exact:true}).click();uncertain=true;await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.waitForTimeout(100);assert.equal(await page.locator('.member-avatars').count(),0);uncertain=false;
await page.locator('.event-row').first().click();expireEvents=true;
await page.getByRole('button',{name:'关闭',exact:true}).click();await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.locator('.event-row').first().click();await page.locator('.login-shell').waitFor();assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
expireEvents=false;await page.getByLabel('演示密码').fill('test-password');await page.getByRole('button',{name:'进入演示',exact:true}).click();await page.locator('.metric').first().waitFor();
failEvents=true;await page.locator('.metric').first().click();await page.locator('.error').waitFor();assert.equal(await page.getByText('暂无数据',{exact:true}).count(),0);failEvents=false;
await page.locator('.sidebar nav button').first().click();await page.locator('.metric').first().waitFor();paginate=true;delayPage=true;await page.locator('.metric').first().click();await page.locator('.event-row').first().waitFor();await page.locator('.load-more').scrollIntoViewIfNeeded();if(!pageStarted)await page.getByRole('button',{name:'加载更多',exact:true}).click();assert.equal(pageStarted,true);await page.locator('.sidebar nav button').first().click();await page.waitForTimeout(650);assert.equal(await page.locator('.error').count(),0);
logoutStatus=500;await page.getByTitle('退出登录').click();await page.getByRole('button',{name:'重试退出',exact:true}).waitFor();assert.equal(await page.evaluate(()=>sessionStorage.getItem('logged-out')),'1');logoutStatus=401;await page.getByRole('button',{name:'重试退出',exact:true}).click();await page.waitForTimeout(100);assert.equal(await page.getByRole('button',{name:'重试退出',exact:true}).count(),0);assert.equal(logoutCalls,2);await page.reload();await page.locator('.login-shell').waitFor();assert.equal(await page.locator('.app-shell').count(),0);
assert.deepEqual(errors,[]);console.log('Session UI passed: preference serialization, access refresh, dates, modal expiry, empty/error, stale pagination, logout retry/401/reload');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
