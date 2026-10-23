const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');const fs=require('node:fs');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const types=['contact_added','contact_deleted','group_left','group_removed'];
const tenants=[{id:1,name:'总部'},{id:2,name:'华东分部'}];
const event={id:1,tenant_id:1,event_type:'group_left',quantity:1,resolution:'exact',enrichment_status:'complete',occurred_at:Date.now(),snapshot:{customer_name:'张女士（模拟客户）',group_name:'客户服务与产品交流长名称测试群',employee_name:'李顾问',demo:true}};
const dashboard={cards:types.map((type,i)=>({type,value:120-i*23,delta:i%2?-3:5})),trend:Array.from({length:30},(_,i)=>({date:new Date(Date.UTC(2026,8,24+i)).toISOString().slice(0,10),...Object.fromEntries(types.map((t,j)=>[t,(i*7+j*3)%20]))})),tops:Object.fromEntries(types.map(t=>[t,[{tenant_id:1,id:'a',name:'客户服务团队（模拟）',count:23}]])),metrics_range:['2026-10-17','2026-10-23'],trend_range:['2026-09-24','2026-10-23'],unresolved_count:2,pending_jobs:1,updated_at:Date.now()};
let longProfile=false;let lastQuery='';await page.route('**/api/v1/**',async route=>{const u=new URL(route.request().url());let data={};
if(u.pathname.endsWith('/config')) data={demo_enabled:true,ding_configured:false};
else if(u.pathname.endsWith('/me'))data={name:longProfile?'user_'+ 'abcdefghijklmnopqrstuvwxyz'.repeat(5):'演示管理员',avatar_url:'https://avatar.example.invalid/test.png',role:'admin',csrf_token:'local-test',tenants:longProfile?[{id:1,name:'tenant_'+ 'abcdefghijklmnopqrstuvwxyz'.repeat(5)}]:tenants};
else if(u.pathname.endsWith('/dashboard')){await new Promise(r=>setTimeout(r,150));data=dashboard}
else if(u.pathname.endsWith('/events')){lastQuery=u.search;data={items:[{...event,event_type:u.searchParams.get('event_type')}],total_records:1,total_quantity:1,next_cursor:null,range:['2026-10-17','2026-10-23']}}
else if(u.pathname.endsWith('/employees'))data=[{id:'employee',tenant_id:1,name:'李顾问'}];
else if(u.pathname.includes('/admin/'))data=[];
await route.fulfill({json:{data}})});


fs.mkdirSync('artifacts/ui-boundary-fixes',{recursive:true});
let healthy=false, imageRequests=0;
await page.route('https://avatar.example.invalid/test.png',async route=>{
 imageRequests++;
 if(!healthy)await route.fulfill({status:503,body:'temporary failure'});
 else await route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH1cAAAAASUVORK5CYII=','base64')});
});
await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:8000');await page.locator('.metric').first().waitFor();
// Crossing a breakpoint must give the same geometry as a fresh draw at that size.
for(const width of [375,1440,390,900,428]) {
 console.log("checking breakpoint",width);
 await page.setViewportSize({width,height:1000});await page.waitForTimeout(450);
 await page.mouse.move(0,0);
 await page.locator('.chart').evaluate(el=>el.scrollIntoView({block:'center'}));await page.waitForTimeout(100);
 const resized=await page.locator('.chart').screenshot();
 await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.waitForTimeout(450);await page.mouse.move(0,0);
 await page.locator('.chart').evaluate(el=>el.scrollIntoView({block:'center'}));await page.waitForTimeout(100);
 const refreshed=await page.locator('.chart').screenshot();
 if(!resized.equals(refreshed)){fs.writeFileSync(`artifacts/ui-boundary-fixes/before-${width}.png`,resized);fs.writeFileSync(`artifacts/ui-boundary-fixes/after-${width}.png`,refreshed);}
 assert.ok(resized.equals(refreshed),`chart geometry after breakpoint ${width}`);
 if(width===375)await page.locator('.trend-panel').screenshot({path:'artifacts/ui-boundary-fixes/chart-resized-375.png'});
}
console.log('PASS chart layout after five breakpoint transitions equals redraw, no refresh required');
// Toggle the first legend, compare blue curve pixels and keep the choice across resizing.
await page.setViewportSize({width:375,height:1000});await page.waitForTimeout(450);
const bluePixels=()=>page.locator('.chart canvas').evaluate(el=>{
 const p=el.getContext('2d').getImageData(0,0,el.width,el.height).data;let n=0;
 for(let i=0;i<p.length;i+=4)if(p[i]===0&&p[i+1]===106&&p[i+2]===214&&p[i+3]>200)n++;
 return n;
});
const visible=await bluePixels();const box=await page.locator('.chart').boundingBox();
await page.mouse.click(box.x+35,box.y+box.height-34);await page.mouse.move(0,0);await page.waitForTimeout(450);
const hidden=await bluePixels();assert.ok(hidden<visible/2,`legend hides blue curve: ${visible} -> ${hidden}`);
await page.setViewportSize({width:1440,height:1000});await page.waitForTimeout(450);
await page.setViewportSize({width:375,height:1000});await page.waitForTimeout(450);
assert.equal(await bluePixels(),hidden,'resize preserves legend selection');
console.log('PASS hidden curve remains hidden across desktop/mobile resizing');
longProfile=true;await page.locator('.mobile-nav button').nth(4).click();await page.waitForTimeout(250);
for(const width of [375,390,428,900,1440]) {
 await page.setViewportSize({width,height:1000});await page.waitForTimeout(200);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`profile overflow ${width}`);
 const nodes=await page.locator('.profile,.profile h2,.profile-tenant,.sidebar-bottom').evaluateAll(els=>els.filter(e=>e.getBoundingClientRect().width>0).map(e=>({width:e.clientWidth,scroll:e.scrollWidth})));
 assert.ok(nodes.every(e=>e.scroll<=e.width+1),`profile or sidebar overflow ${width}`);
 assert.ok((await page.locator('.profile h2').innerText()).includes('abcdefghijklmnopqrstuvwxyz'.repeat(5)));
 await page.screenshot({path:`artifacts/ui-boundary-fixes/profile-${width}.png`,fullPage:true});
 if(width===375)await page.locator('.profile').screenshot({path:'artifacts/ui-boundary-fixes/profile-long-text.png'});
 if(width===900){assert.ok((await page.locator('.sidebar-user-name').getAttribute('title')).includes('abcdefghijklmnopqrstuvwxyz'.repeat(5)));}
 if(width===900)await page.locator('.sidebar-bottom').screenshot({path:'artifacts/ui-boundary-fixes/sidebar-long-text.png'});
}
console.log('PASS full long names and tenant names preserved without profile/sidebar overflow at five widths');
assert.equal(await page.locator('.profile .avatar img').count(),0);
let before=imageRequests;await page.waitForTimeout(250);assert.equal(imageRequests,before,'no automatic retry loop');
await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.waitForTimeout(250);
assert.ok(imageRequests>before,'explicit refresh retries failed image');
before=imageRequests;await page.waitForTimeout(300);assert.equal(imageRequests,before,'persistent failure does not loop');
healthy=true;await page.getByRole('button',{name:'刷新数据',exact:false}).click();
for(const img of await page.locator('.profile .avatar img,.sidebar-bottom .avatar img').all())await img.evaluate(async el=>{await el.decode();if(!el.complete||el.naturalWidth!==1)throw new Error('retry image not decoded')});
assert.equal(await page.locator('.profile .avatar img').count(),1);
before=imageRequests;await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.waitForTimeout(250);
assert.equal(imageRequests,before,'healthy images are not reloaded');
console.log('PASS explicit avatar retry after same URL failure, decoded recovery, no retry loop or healthy image reload');
let mediaHealthy=false,mediaRequests=0;
await page.route('**/api/v1/media/*',async route=>{
 mediaRequests++;
 if(!mediaHealthy)await route.fulfill({status:503,body:'temporary media failure'});
 else await route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH1cAAAAASUVORK5CYII=','base64')});
});
event.snapshot.customer_media_id=123;event.snapshot.employee_media_id=124;
await page.locator('.sidebar nav button').nth(1).click();await page.locator('.event-row').first().waitFor();await page.waitForTimeout(200);
assert.equal(await page.locator('.event-row .avatar img').count(),0);
const mediaBefore=mediaRequests;mediaHealthy=true;
await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.waitForTimeout(200);
assert.ok(mediaRequests>mediaBefore);
assert.equal(await page.locator('.event-row .avatar img').count(),2);
for(const img of await page.locator('.event-row .avatar img').all())await img.evaluate(async el=>{await el.decode();if(!el.complete||el.naturalWidth!==1)throw new Error('media retry did not load')});
assert.deepEqual(await page.locator('.event-row .avatar img').evaluateAll(els=>els.map(el=>new URL(el.src).pathname)),['/api/v1/media/123','/api/v1/media/124']);
console.log('PASS local customer/employee media IDs retained and decode after explicit retry (mock media)');

assert.deepEqual(errors,[]);
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
