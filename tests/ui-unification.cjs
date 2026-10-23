const artifactDir=process.env.UI_ARTIFACT_DIR || 'artifacts/ui-unification';
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');const fs=require('node:fs');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const types=['contact_added','contact_deleted','group_left','group_removed'];
const tenants=[{id:1,name:'总部'},{id:2,name:'华东分部'}];
const event={id:1,tenant_id:1,event_type:'group_left',quantity:1,resolution:'exact',enrichment_status:'complete',occurred_at:Date.now(),snapshot:{customer_name:'张女士（模拟客户）',group_name:'客户服务与产品交流长名称测试群',employee_name:'李顾问',demo:true}};
const dashboard={cards:types.map((type,i)=>({type,value:120-i*23,delta:i%2?-3:5})),trend:Array.from({length:30},(_,i)=>({date:new Date(Date.UTC(2026,8,24+i)).toISOString().slice(0,10),...Object.fromEntries(types.map((t,j)=>[t,(i*7+j*3)%20]))})),tops:Object.fromEntries(types.map(t=>[t,[{tenant_id:1,id:'a',name:'客户服务团队（模拟）',count:23}]])),metrics_range:['2026-10-17','2026-10-23'],trend_range:['2026-09-24','2026-10-23'],unresolved_count:2,pending_jobs:1,updated_at:Date.now()};
let lastQuery='';await page.route('**/api/v1/**',async route=>{const u=new URL(route.request().url());let data={};
if(u.pathname.endsWith('/config')) data={demo_enabled:true,ding_configured:false};
else if(u.pathname.endsWith('/me'))data={name:'演示管理员',role:'admin',csrf_token:'local-test',tenants};
else if(u.pathname.endsWith('/dashboard')){await new Promise(r=>setTimeout(r,150));data=dashboard}
else if(u.pathname.endsWith('/events')){lastQuery=u.search;data={items:[{...event,event_type:u.searchParams.get('event_type')}],total_records:1,total_quantity:1,next_cursor:null,range:['2026-10-17','2026-10-23']}}
else if(u.pathname.endsWith('/employees'))data=[{id:'employee',tenant_id:1,name:'李顾问'}];
else if(u.pathname.includes('/admin/'))data=[];
await route.fulfill({json:{data}})});
fs.mkdirSync(artifactDir,{recursive:true});await page.goto('http://localhost:8000');await page.locator('.metric').first().waitFor();
for(const width of [1440,900,375,390,428]){await page.setViewportSize({width,height:900});await page.waitForTimeout(150);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`overflow ${width}`);await page.screenshot({path:`${artifactDir}/dashboard-${width}.png`,fullPage:true});}
await page.setViewportSize({width:1440,height:1000});
await page.locator('.chart canvas').first().waitFor();const canvas=await page.locator('.chart canvas').first().elementHandle();await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.waitForFunction(()=>!document.querySelector('.loading-status'));assert.equal(await canvas.evaluate(el=>el.isConnected),true,'refresh retains canvas');
await page.locator('.metric').first().click();await page.locator('.event-row').first().waitFor();assert.equal(await page.locator('input[type=date]').count(),0);await page.locator('.detail-toolbar select').first().selectOption('[1,"employee"]');await page.locator('.filters select').selectOption('2');await page.waitForTimeout(300);assert.ok(!lastQuery.includes('employee_id'),'tenant clears employee');
const row=page.locator('.event-row').first();await row.click();await page.getByRole('dialog').waitFor();assert.equal(await page.evaluate(()=>document.body.style.overflow),'hidden');await page.keyboard.press('Escape');assert.equal(await page.getByRole('dialog').count(),0);assert.equal(await row.evaluate(el=>el===document.activeElement),true);
await page.setViewportSize({width:390,height:844});await page.locator('.mobile-nav button').nth(3).click();await page.locator('.event-row').first().waitFor();assert.ok((await page.locator('.event-row').innerText()).includes('张女士'));await page.screenshot({path:`${artifactDir}/groups-390.png`,fullPage:true});
assert.deepEqual(errors,[]);fs.writeFileSync(`${artifactDir}/verification.json`,JSON.stringify({mode:'mock API, local browser only',widths:[1440,900,375,390,428],checks:['no horizontal overflow','same-query refresh retains canvas','tenant clears employee filter','detail inherits dates','Escape and focus restore','scroll lock','group member visible'],errors},null,2));console.log('UI regression passed: five widths, refresh, tenant, dates, dialog, group identity');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
