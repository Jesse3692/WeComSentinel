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

fs.mkdirSync('artifacts/ultrawide',{recursive:true});
dashboard.tops=Object.fromEntries(types.map(t=>[t,Array.from({length:5},(_,i)=>({tenant_id:1,id:'staff'+i,name:'客户服务团队 '+(i+1)+'（模拟）',count:23-i*3}))]));
await page.setViewportSize({width:3440,height:1440});await page.goto('http://localhost:8000');await page.locator('.metric').first().waitFor();
for(const [width,height] of [[3440,1440],[2752,1152],[2293,960],[2560,1080],[1920,1080],[1440,900],[900,900],[393,850]]){
 await page.setViewportSize({width,height});await page.waitForTimeout(250);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`overflow ${width}`);
 const main=await page.locator('main').boundingBox(),chart=await page.locator('.trend-panel').boundingBox(),ranks=await page.locator('.rankings').boundingBox();
 if(width>=1800)assert.ok(Math.abs(main.x+main.width-width)<=1,`workspace fills available width ${width}`);
 if(width>=2200){assert.ok(ranks.x>=chart.x+chart.width,`ranking column beside trend ${width}`);assert.equal(await page.locator('.rankings .panel').count(),3);if(width===3440){const lastRank=await page.locator('.rankings .panel').last().boundingBox();assert.ok(lastRank.y+lastRank.height<=height,'all three full Top5 panels fit 3440x1440');}}
 else assert.ok(ranks.y>=chart.y+chart.height,`standard layout preserved ${width}`);
 const sizes=await page.locator('.chart').evaluate(el=>({w:el.clientWidth,c:el.querySelector('canvas').clientWidth}));assert.ok(Math.abs(sizes.w-sizes.c)<=1,'canvas follows available width');
 await page.screenshot({path:`artifacts/ultrawide/dashboard-${width}.png`,fullPage:true});
}
// Hiding a curve survives transition from a phone to the wide two-column layout.
await page.setViewportSize({width:3440,height:1440});await page.waitForTimeout(200);
const pixels=()=>page.locator('.chart canvas').evaluate(el=>{const d=el.getContext('2d').getImageData(0,0,el.width,el.height).data;let n=0;for(let i=0;i<d.length;i+=4)if(d[i]===0&&d[i+1]===106&&d[i+2]===214&&d[i+3]>200)n++;return n;});
const visible=await pixels();const canvas=await page.locator('.chart canvas').elementHandle();
await page.setViewportSize({width:375,height:1000});await page.waitForTimeout(250);await page.locator('.chart').scrollIntoViewIfNeeded();const cb=await page.locator('.chart').boundingBox();await page.mouse.click(cb.x+35,cb.y+cb.height-34);await page.mouse.move(0,0);
await page.setViewportSize({width:3440,height:1440});await page.waitForTimeout(250);assert.ok(await pixels()<visible/2,'hidden curve remains hidden on ultrawide');assert.ok(await canvas.evaluate(el=>el.isConnected),'chart canvas retained');
console.log('PASS eight viewport sizes including ultrawide and scaled workspaces; responsive trend/rank layout and preserved hidden curve');
await page.locator('.sidebar nav button').nth(1).click();await page.locator('.event-row').waitFor();
for(const [width,height] of [[3440,1440],[2752,1152],[2293,960],[1920,1080],[1440,900],[393,850]]){
 await page.setViewportSize({width,height});await page.waitForTimeout(200);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 const list=await page.locator('.event-list').boundingBox(),dock=await page.locator('.pagination').boundingBox();assert.ok(Math.abs(list.x-dock.x)<=1&&Math.abs(list.width-dock.width)<=1,`dock aligns with list ${width}`);
 if(width>=1800){
  assert.ok(await page.locator('.event-columns').isVisible());
  const staff=await page.locator('.event-row .employee-identity').boundingBox(),head=await page.getByText('员工与主体',{exact:true}).boundingBox();assert.ok(Math.abs(staff.x-head.x)<=1,`employee column aligned ${width}`);
 }else assert.equal(await page.locator('.event-columns').isVisible(),false);
 await page.locator('.event-row').click();await page.getByRole('dialog').waitFor();const close=await page.getByRole('button',{name:'关闭',exact:true}).boundingBox();assert.ok(close.x>=0&&close.y>=0&&close.x+close.width<=width&&close.y+close.height<=height);
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.screenshot({path:`artifacts/ultrawide/contacts-${width}.png`});
}
console.log('PASS wide contact columns, aligned dock, readable dialog; standard/mobile layouts retained');
// Keep long customer and employee text visible within the wide grid.
event.snapshot.customer_name='客户姓名'+ 'abcdefghijklmnopqrstuvwxyz'.repeat(12);event.snapshot.employee_name='员工姓名'+ 'abcdefghijklmnopqrstuvwxyz'.repeat(12);
await page.setViewportSize({width:3440,height:1440});await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.waitForTimeout(250);
const overflow=await page.locator('.event-row,.event-content,.employee-identity').evaluateAll(els=>els.some(e=>e.scrollWidth>e.clientWidth+1));assert.equal(overflow,false,'long names contained');
assert.ok((await page.locator('.event-row').innerText()).includes(event.snapshot.customer_name));assert.ok((await page.locator('.event-row').innerText()).includes(event.snapshot.employee_name));
assert.deepEqual(errors,[]);console.log('PASS full continuous long names preserved without horizontal overflow');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
