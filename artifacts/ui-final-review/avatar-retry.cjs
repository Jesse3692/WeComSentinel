const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');const fs=require('node:fs');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const types=['contact_added','contact_deleted','group_left','group_removed'];
const tenants=[{id:1,name:'总部'},{id:2,name:'华东分部'}];
const event={id:1,tenant_id:1,event_type:'group_left',quantity:1,resolution:'exact',enrichment_status:'complete',occurred_at:Date.now(),snapshot:{customer_name:'张女士（模拟客户）',group_name:'客户服务与产品交流长名称测试群',employee_name:'李顾问',demo:true}};
const dashboard={cards:types.map((type,i)=>({type,value:120-i*23,delta:i%2?-3:5})),trend:Array.from({length:30},(_,i)=>({date:new Date(Date.UTC(2026,8,24+i)).toISOString().slice(0,10),...Object.fromEntries(types.map((t,j)=>[t,(i*7+j*3)%20]))})),tops:Object.fromEntries(types.map(t=>[t,[{tenant_id:1,id:'a',name:'客户服务团队（模拟）',count:23}]])),metrics_range:['2026-10-17','2026-10-23'],trend_range:['2026-09-24','2026-10-23'],unresolved_count:2,pending_jobs:1,updated_at:Date.now()};
let meCalls=0;let lastQuery='';await page.route('**/api/v1/**',async route=>{const u=new URL(route.request().url());let data={};
if(u.pathname.endsWith('/config')) data={demo_enabled:true,ding_configured:false};
else if(u.pathname.endsWith('/me')){meCalls++;data={avatar_url:'https://avatar.example.invalid/test.png',name:'演示管理员',role:'admin',csrf_token:'local-test',tenants};}
else if(u.pathname.endsWith('/dashboard')){await new Promise(r=>setTimeout(r,150));data=dashboard}
else if(u.pathname.endsWith('/events')){lastQuery=u.search;data={items:[{...event,event_type:u.searchParams.get('event_type')}],total_records:1,total_quantity:1,next_cursor:null,range:['2026-10-17','2026-10-23']}}
else if(u.pathname.endsWith('/employees'))data=[{id:'employee',tenant_id:1,name:'李顾问'}];
else if(u.pathname.includes('/admin/'))data=[];
await route.fulfill({json:{data}})});


let imageRequests=0, healthy=false;
await page.route('https://avatar.example.invalid/test.png',async route=>{
 imageRequests++;
 if(!healthy)await route.fulfill({status:503,body:'temporary failure'});
 else await route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH1cAAAAASUVORK5CYII=','base64')});
});
await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:8000');await page.locator('.metric').first().waitFor();
await page.locator('.sidebar nav button').nth(4).click();await page.waitForTimeout(250);
assert.equal(await page.locator('.profile .avatar img').count(),0);
const beforeImages=imageRequests,beforeMe=meCalls;
healthy=true;await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.waitForTimeout(300);
console.log(JSON.stringify({meBefore:beforeMe,meAfter:meCalls,imageRequestsBefore:beforeImages,imageRequestsAfter:imageRequests,profileImages:await page.locator('.profile .avatar img').count()}));
assert.ok(meCalls>beforeMe);
assert.equal(imageRequests,beforeImages);
assert.equal(await page.locator('.profile .avatar img').count(),0);
await page.reload();await page.locator('.metric').first().waitFor();await page.locator('.sidebar nav button').nth(4).click();
await page.locator('.profile .avatar img').evaluate(async el=>{await el.decode();if(!el.complete || el.naturalWidth<1)throw new Error('fixture not recovered')});
console.log('confirmed: recovered image server loads after reload; profile refresh with unchanged URL leaves failed avatar in placeholder');
assert.deepEqual(errors,[]);
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
