const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');const fs=require('node:fs');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const types=['contact_added','contact_deleted','group_left','group_removed'];
const tenants=[{id:1,name:'总部'},{id:2,name:'华东分部'}];
const event={id:1,tenant_id:1,event_type:'group_left',quantity:1,resolution:'exact',enrichment_status:'complete',occurred_at:Date.now(),snapshot:{customer_name:'张女士（模拟客户）',group_name:'客户服务与产品交流长名称测试群',employee_name:'李顾问',demo:true}};
const dashboard={cards:types.map((type,i)=>({type,value:120-i*23,delta:i%2?-3:5})),trend:Array.from({length:366},(_,i)=>({date:new Date(Date.UTC(2025,9,23+i)).toISOString().slice(0,10),...Object.fromEntries(types.map((t,j)=>[t,(i*7+j*3)%20]))})),tops:Object.fromEntries(types.map(t=>[t,[{tenant_id:1,id:'a',name:'employee_'+ 'abcdefghijklmnopqrstuvwxyz'.repeat(5),count:23}]])),metrics_range:['2026-10-17','2026-10-23'],trend_range:['2025-10-23','2026-10-23'],unresolved_count:2,pending_jobs:1,updated_at:Date.now()};
let meCalls=0, profileMode='original', delayMe=0, meStarted=false;let lastQuery='';await page.route('**/api/v1/**',async route=>{const u=new URL(route.request().url());let data={};
if(u.pathname.endsWith('/config')) data={demo_enabled:true,ding_configured:false};
else if(u.pathname.endsWith('/me')) {
 meCalls++;meStarted=true;
 const mode=profileMode;
 if(delayMe)await new Promise(r=>setTimeout(r,delayMe));
 if(mode==='error'){await route.fulfill({status:500,json:{error:{message:'模拟资料读取失败'}}});return;}
 data={name:mode==='updated'?'刷新后的用户':'演示管理员',role:'admin',csrf_token:'local-test',tenants:mode==='updated'?[{id:1,name:'更新后的主体'}]:tenants};
}
else if(u.pathname.endsWith('/dashboard')){await new Promise(r=>setTimeout(r,150));data=dashboard}
else if(u.pathname.endsWith('/events')){lastQuery=u.search;data={items:[{...event,event_type:u.searchParams.get('event_type')}],total_records:1,total_quantity:1,next_cursor:null,range:['2026-10-17','2026-10-23']}}
else if(u.pathname.endsWith('/employees'))data=[{id:'employee',tenant_id:1,name:'李顾问'}];
else if(u.pathname.includes('/admin/'))data=[];
await route.fulfill({json:{data}})});


fs.mkdirSync('artifacts/ui-fixes',{recursive:true});
await page.goto('http://localhost:8000');await page.locator('.metric').first().waitFor();
for(const width of [1440,900,375,390,428]) {
 await page.setViewportSize({width,height:900});await page.waitForTimeout(250);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`page overflow ${width}`);
 const rows=await page.locator('.rank-row').evaluateAll(els=>els.map(e=>({width:e.clientWidth,scroll:e.scrollWidth,text:e.innerText})));
 assert.equal(rows.length,3);
 assert.ok(rows.every(e=>e.scroll<=e.width+1),`ranking overflow ${width}`);
 assert.ok(rows.every(e=>e.text.includes('abcdefghijklmnopqrstuvwxyz'.repeat(5))),'full names retained');
 assert.equal(await page.locator('h1').innerText(),'客户关系汇总');
 assert.ok((await page.locator('.rankings h2').first().innerText()).includes('新增客户员工'));
 assert.ok((await page.locator('.page-footer').innerText()).includes('更新汇总'));
 await page.screenshot({path:`artifacts/ui-fixes/dashboard-${width}.png`,fullPage:true});
 if(width===375)await page.locator('.rankings .panel').first().screenshot({path:'artifacts/ui-fixes/long-name-ranking.png'});
}
console.log('PASS long continuous employee IDs retained and contained at five widths; summary and ranking labels');
await page.setViewportSize({width:1440,height:1000});await page.waitForTimeout(200);
await page.locator('.chart').scrollIntoViewIfNeeded();const box=await page.locator('.chart').boundingBox();
for(const [x,date] of [[box.x+38,'2025-10-23'],[box.x+box.width-18,'2026-10-23']]) {
 await page.mouse.move(x,box.y+50);await page.waitForTimeout(200);
 assert.ok((await page.locator('.chart').innerText()).includes(date),`full tooltip date ${date}`);
}
await page.locator('.trend-panel').screenshot({path:'artifacts/ui-fixes/cross-year-tooltip.png'});
console.log('PASS same month/day in different years has distinct full tooltip dates');
const nav=page.locator('.sidebar nav');
await page.locator('.filters select').selectOption('2');await page.waitForTimeout(250);
await nav.locator('button').nth(4).click();await page.waitForTimeout(200);
const before=meCalls;profileMode='updated';
await page.getByRole('button',{name:'刷新数据',exact:false}).click();
await page.getByRole('heading',{name:'刷新后的用户',exact:true}).waitFor();
assert.ok(meCalls>before,'profile refresh requests /me');
assert.equal(await page.locator('.profile-tenant').count(),1);
assert.ok((await page.locator('.profile-tenant').innerText()).includes('更新后的主体'));
await page.locator('.profile').screenshot({path:'artifacts/ui-fixes/profile-refreshed.png'});
await nav.locator('button').first().click();await page.waitForTimeout(250);
assert.equal(await page.locator('.filters select').inputValue(),'all','revoked selection falls back');
await nav.locator('button').nth(4).click();await page.waitForTimeout(200);
profileMode='error';await page.getByRole('button',{name:'刷新数据',exact:false}).click();
await page.getByRole('alert').waitFor();assert.ok((await page.locator('.profile').innerText()).includes('刷新后的用户'));
profileMode='updated';await page.getByRole('button',{name:'重试',exact:true}).click();await page.waitForTimeout(200);
assert.equal(await page.getByRole('alert').count(),0);
console.log('PASS profile refresh updates name/tenants, revoked selection falls back, errors retain profile and retry works');
profileMode='original';delayMe=500;meStarted=false;
await page.getByRole('button',{name:'刷新数据',exact:false}).click();
while(!meStarted)await page.waitForTimeout(10);
// Navigation reloads access context. Only the old pending response is stale.
profileMode='updated';
await nav.locator('button').first().click();await page.waitForTimeout(700);
assert.ok((await page.locator('.sidebar-bottom').innerText()).includes('刷新后的用户'),'late profile ignored after switching page');
profileMode='updated';delayMe=0;await nav.locator('button').nth(4).click();await page.waitForTimeout(200);
await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.getByRole('heading',{name:'刷新后的用户',exact:true}).waitFor();
profileMode='original';delayMe=500;meStarted=false;
await page.getByRole('button',{name:'刷新数据',exact:false}).click();
while(!meStarted)await page.waitForTimeout(10);
await page.getByRole('button',{name:'退出登录',exact:true}).click();await page.locator('.login-shell').waitFor();await page.waitForTimeout(650);
assert.equal(await page.locator('.app-shell').count(),0,'late /me does not restore logged-out session');
assert.deepEqual(errors,[]);
console.log('PASS late profile response cannot restore a logged-out session; no page JS errors');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
