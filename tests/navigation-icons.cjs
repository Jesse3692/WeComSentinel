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

fs.mkdirSync('artifacts/navigation',{recursive:true});
await page.goto('http://localhost:8000');await page.locator('.metric').first().waitFor();
const expected=['汇总','新增','删除','群变更','我的'];
let desktopPaths;
for(const width of [1440,900,375,390,428]) {
 await page.setViewportSize({width,height:900});await page.waitForTimeout(200);
 const nav=page.locator(width>760?'.sidebar nav':'.mobile-nav');
 assert.equal(await nav.isVisible(),true);
 assert.deepEqual(await nav.locator('button').allTextContents(),expected.map((n,i)=>width>760&&i===0?n+'●':n));
 assert.equal(await nav.locator('svg.nav-icon').count(),5);
 const paths=await nav.locator('svg path').evaluateAll(els=>els.map(e=>e.getAttribute('d')));
 assert.ok(paths.every(Boolean));assert.equal(new Set(paths).size,5);
 if(width===1440)desktopPaths=paths;else assert.deepEqual(paths,desktopPaths);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`overflow at ${width}px`);
 for(const index of [1,2,3,4,0]) {
  await nav.locator('button').nth(index).click();
  await page.waitForFunction(()=>!document.querySelector('.loading-status'));
  assert.equal(await nav.locator('button.active').count(),1);
  assert.equal(await nav.locator('button').nth(index).getAttribute('class'),'active');
 }
 await page.screenshot({path:`artifacts/navigation/navigation-${width}.png`,fullPage:true});
 if(width===1440)await page.locator('.sidebar').screenshot({path:'artifacts/navigation/sidebar.png'});
 if(width===390)await nav.screenshot({path:'artifacts/navigation/mobile-nav.png'});
}
assert.deepEqual(errors,[]);
console.log('PASS: five SVG icons, labels, shared paths, selection and overflow at 1440/900/375/390/428px (mock API)');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
