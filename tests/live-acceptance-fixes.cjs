const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');const fs=require('node:fs');
const out='artifacts/live-fixes';fs.mkdirSync(out,{recursive:true});
(async()=>{const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL||'msedge'});try{
 const page=await browser.newPage({viewport:{width:393,height:760},isMobile:true,hasTouch:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let signedIn=true,granted=true,failMe=false,mode='user',paginate=false,latePage=false,pageStarted=false,releasePage;
 const gate=new Promise(resolve=>releasePage=resolve);
 const event={id:1,tenant_id:1,event_type:'contact_added',quantity:1,occurred_at:Date.now(),resolution:'exact',enrichment_status:'complete',add_way:'0',snapshot:{customer_name:'模拟客户',employee_name:'模拟员工',note:'长资料'.repeat(400)}};
 const types=['contact_added','contact_deleted','group_left','group_removed'];
 const dashboard={cards:types.map(type=>({type,value:3,delta:1})),trend:[{date:'2026-10-23',...Object.fromEntries(types.map(t=>[t,3]))}],tops:Object.fromEntries(types.map(t=>[t,[]])),metrics_range:['2026-10-17','2026-10-23'],trend_range:['2026-09-24','2026-10-23'],updated_at:Date.now()};
 await page.route('**/api/v1/**',async route=>{
  const u=new URL(route.request().url());let data={};
  const reply=(status,message)=>route.fulfill({status,json:{error:{message}}});
  if(u.pathname.endsWith('/config'))data={demo_enabled:true,ding_configured:false};
  else if(u.pathname.endsWith('/auth/demo')){if(!granted)return reply(403,'无访问权限，请联系管理员');signedIn=true;data={};}
  else if(u.pathname.endsWith('/auth/logout')){signedIn=false;data={};}
  else if(u.pathname.endsWith('/me')){if(!signedIn)return reply(401,'登录已过期');if(failMe)return reply(503,'模拟断网');data={name:'模拟用户',role:mode,csrf_token:'local-test',tenants:granted?[{id:1,name:'测试主体'}]:[]};}
  else if(u.pathname.endsWith('/dashboard')){if(!granted)return reply(403,'无访问权限，请联系管理员');data=dashboard;}
  else if(u.pathname.endsWith('/events')){
   if(u.searchParams.has('cursor')&&latePage){pageStarted=true;await gate;}
   data={items:[{...event,id:u.searchParams.has('cursor')?2:1}],next_cursor:paginate&&!u.searchParams.has('cursor')?'next':null,total_records:paginate?2:1,total_quantity:paginate?2:1,range:['2026-10-17','2026-10-23']};
  }
  else if(u.pathname.endsWith('/employees'))data=[];
  await route.fulfill({json:{data}});
 });
 await page.goto('http://localhost:8000');await page.locator('.metric').first().waitFor();
 // A transient context lookup failure must keep known data, with an error.
 failMe=true;await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.getByRole('alert').waitFor();
 assert.equal(await page.locator('.metric').count(),4);failMe=false;
 await page.locator('.mobile-nav button').nth(1).click();await page.locator('.event-row').waitFor();
 assert.ok((await page.locator('.event-row').innerText()).includes('方式未知'),'source code 0 label');
 for(const [width,height] of [[375,640],[393,760],[428,640],[760,360]]){
  await page.setViewportSize({width,height});await page.locator('.event-row').click();await page.getByRole('dialog').waitFor();
  const check=async()=>{
   const b=await page.getByRole('button',{name:'关闭',exact:true}).boundingBox();
   assert.ok(b&&b.x>=0&&b.y>=0&&b.x+b.width<=width+1&&b.y+b.height<=height+1,`close in viewport ${width}x${height}`);
   assert.ok(b.width>=44&&b.height>=44,'touch target');
   const visible=await page.getByRole('button',{name:'关闭',exact:true}).evaluate(el=>{const b=el.getBoundingClientRect();return el.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));});assert.ok(visible,'close not covered');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'no page overflow');
  };
  await check();if(width===393)await page.screenshot({path:out+'/modal-initial-393.png'});await page.locator('.modal-body').evaluate(el=>{el.scrollTop=el.scrollHeight});await check();
  if(width===393)await page.screenshot({path:out+'/modal-393.png'});
  await page.getByRole('button',{name:'关闭',exact:true}).click();assert.notEqual(await page.evaluate(()=>document.body.style.overflow),'hidden');
  await page.locator('.event-row').click();await check();assert.equal(await page.locator('.modal-body').evaluate(el=>el.scrollTop),0,'reopen starts at top');
  await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});
 }
 console.log('PASS mock UI: code0 label; modal close visible/clickable before and after scroll at four sizes, reopen/Escape/unlock');
 await page.setViewportSize({width:393,height:760});
 // A stale successful page arriving after revocation cannot restore old records.
 paginate=true;latePage=true;await page.getByRole('button',{name:'刷新数据',exact:false}).click();
 await page.getByRole('button',{name:'下一页',exact:true}).click();
 for(let i=0;i<100&&!pageStarted;i++)await page.waitForTimeout(50);
 assert.ok(pageStarted,'next page request started');
 granted=false;await page.locator('.mobile-nav button').first().click();await page.locator('.access-denied').waitFor();releasePage();await page.waitForTimeout(100);
 assert.equal(await page.locator('.app-shell,.metric,.event-row,.mobile-nav,.modal').count(),0);
 assert.ok((await page.locator('.access-denied').innerText()).includes('无访问权限'));
 await page.reload();await page.locator('.access-denied').waitFor();assert.equal(await page.locator('.app-shell').count(),0,'reentry with old cookie stays blocked');
 for(const width of [375,393,428,900,1440]){
  await page.setViewportSize({width,height:800});const b=await page.locator('.login-card').boundingBox();
  assert.ok(Math.abs(b.x+b.width/2-width/2)<1,`no-access centered ${width}`);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 }
 await page.setViewportSize({width:393,height:760});await page.screenshot({path:out+'/no-access-393.png'});
 // Recovery is explicit; restoration must not require corrupting/replacing sessions.
 granted=true;await page.getByRole('button',{name:'重新检查授权'}).click();await page.locator('.metric').first().waitFor();
 granted=false;await page.getByRole('button',{name:'刷新数据',exact:false}).click();await page.locator('.access-denied').waitFor();
 assert.equal(await page.locator('.metric,.event-row,.filters,.profile').count(),0);
 await page.getByRole('button',{name:'退出登录',exact:true}).click();await page.locator('.login-shell').waitFor();
 // Login failure itself also uses a centered, contained card.
 await page.locator('input[type=password]').fill('mock-only');await page.getByRole('button',{name:'进入演示',exact:true}).click();await page.getByRole('alert').waitFor();
 for(const width of [375,393,428]){
  await page.setViewportSize({width,height:740});const b=await page.locator('.login-card').boundingBox();assert.ok(Math.abs(b.x+b.width/2-width/2)<1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 }
 await page.setViewportSize({width:393,height:760});await page.screenshot({path:out+'/login-denied-393.png'});
 assert.deepEqual(errors,[]);
 console.log('PASS mock UI: zero grants on entry/refresh block business UI; centered login/no-access; offline retention; explicit restored-grant refresh');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
