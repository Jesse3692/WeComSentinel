const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');const fs=require('node:fs');
const out=process.env.UI_ARTIFACT_DIR || 'artifacts/contact-search-pages';fs.mkdirSync(out,{recursive:true});
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const employees=[{id:'staff-A',name:'张三',tenant_id:1},{id:'staff-B',name:'李四',tenant_id:1},{id:'staff-C',name:'王五',tenant_id:1}];
 const all=Array.from({length:45},(_,i)=>({id:i+1,tenant_id:1,employee_id:employees[i%3].id,quantity:1,occurred_at:Date.now(),resolution:'exact',enrichment_status:'complete',snapshot:{employee_name:employees[i%3].name,customer_name:'模拟客户'+String(i+1).padStart(3,'0')}}));
 const requests=[];let failNext=false,delayNext=false,nextStarted=false;let released;const gate=new Promise(r=>released=r);
 await page.route('**/api/v1/**',async route=>{
  const u=new URL(route.request().url());let data={};
  if(u.pathname.endsWith('/config'))data={demo_enabled:true,ding_configured:false};
  else if(u.pathname.endsWith('/me'))data={name:'模拟管理员',role:'admin',csrf_token:'mock-only',tenants:[{id:1,name:'模拟主体'}]};
  else if(u.pathname.endsWith('/dashboard'))data={cards:[],trend:[],tops:{contact_added:[],contact_deleted:[],group_left:[]},metrics_range:['2026-10-17','2026-10-23'],trend_range:['2026-09-24','2026-10-23'],updated_at:Date.now()};
  else if(u.pathname.endsWith('/employees'))data=employees.filter(e=>(e.name+e.id).toLowerCase().includes((u.searchParams.get('keyword')||'').toLowerCase()));
  else if(u.pathname.endsWith('/events')){
   const q=u.searchParams,keyword=(q.get('employee_keyword')||'').toLowerCase(),exact=q.get('employee_id');requests.push(Object.fromEntries(q));
   const cursor=q.get('cursor')||'p0',offset=Number(cursor.slice(1)),limit=Number(q.get('limit')||20);
   if(delayNext&&offset>0){nextStarted=true;await gate;}
   if(failNext&&offset>0){await route.fulfill({status:503,json:{error:{message:'模拟翻页失败'}}});return;}
   const rows=all.filter(r=>(!keyword||(r.snapshot.employee_name+r.employee_id).toLowerCase().includes(keyword))&&(!exact||r.employee_id===exact));
   data={items:rows.slice(offset,offset+limit).map(r=>({...r,event_type:q.get('event_type')})),total_records:rows.length,total_quantity:rows.length,page_cursor:cursor,next_cursor:offset+limit<rows.length?'p'+(offset+limit):null,range:['2026-10-17','2026-10-23']};
  }
  await route.fulfill({json:{data}});
 });
 await page.goto(process.env.UI_SMOKE_URL || 'http://localhost:8000');await page.locator('.app-shell').waitFor();
 const waitSummary=async text=>{await page.locator('.page-summary').filter({hasText:text}).waitFor();await page.waitForFunction(()=>!document.querySelector('.loading-status'));};
 for(const width of [1440,393]){
  await page.setViewportSize({width,height:850});
  const nav=()=>page.locator(width>760?'.sidebar nav button':'.mobile-nav button');
  for(const [index,type] of [[1,'contact_added'],[2,'contact_deleted']]){
   await nav().nth(index).click();await page.locator('.event-row').first().waitFor();
   await page.getByRole('button',{name:'清空',exact:true}).click();await page.getByLabel('每页记录数').selectOption('20');await waitSummary('共 45 条');
   assert.equal(await page.locator('.event-row').count(),20);assert.equal(await page.getByRole('button',{name:'上一页',exact:true}).isDisabled(),true);
   await page.getByRole('button',{name:'下一页',exact:true}).click();await waitSummary('第 2 / 3 页');
   assert.equal(await page.locator('.event-row').count(),20);assert.ok((await page.locator('.event-row').first().innerText()).includes('模拟客户021'));
   await page.getByRole('button',{name:'下一页',exact:true}).click();await waitSummary('第 3 / 3 页');assert.equal(await page.locator('.event-row').count(),5);assert.equal(await page.getByRole('button',{name:'下一页',exact:true}).isDisabled(),true);
   await page.getByRole('button',{name:'上一页',exact:true}).click();await waitSummary('第 2 / 3 页');
   await page.getByRole('button',{name:'上一页',exact:true}).click();await waitSummary('第 1 / 3 页');assert.ok((await page.locator('.event-row').first().innerText()).includes('模拟客户001'));
   await page.getByLabel('每页记录数').selectOption('10');await waitSummary('第 1 / 5 页');
   await page.getByRole('button',{name:'下一页',exact:true}).click();await waitSummary('第 2 / 5 页');
   const input=page.getByLabel('搜索员工',{exact:true});
   await input.dispatchEvent('compositionstart');await input.fill('张三');await input.dispatchEvent('compositionend');await waitSummary('第 1 / 2 页 · 共 15 条');
   assert.ok((await page.locator('.event-row').allInnerTexts()).every(t=>t.includes('张三')),'Chinese keyword filters records without dropdown');
   await page.getByRole('button',{name:'下一页',exact:true}).click();await waitSummary('第 2 / 2 页');assert.equal(await page.locator('.event-row').count(),5);
   await input.fill('STAFF-b');await page.getByRole('button',{name:'搜索',exact:true}).click();await waitSummary('第 1 / 2 页 · 共 15 条');assert.ok((await page.locator('.event-row').allInnerTexts()).every(t=>t.includes('李四')));
   await input.fill('zzzz_no_employee');await input.press('Enter');await waitSummary('共 0 条');assert.equal(await page.locator('.event-row').count(),0);assert.equal(await page.getByRole('button',{name:'下一页',exact:true}).isDisabled(),true);
   await page.getByRole('button',{name:'清空',exact:true}).click();await waitSummary('共 45 条');
   await page.getByLabel('指定员工',{exact:true}).selectOption(JSON.stringify([1,'staff-C']));await waitSummary('共 15 条');assert.ok((await page.locator('.event-row').allInnerTexts()).every(t=>t.includes('王五')));
   await page.getByRole('button',{name:'清空',exact:true}).click();await waitSummary('共 45 条');
   failNext=true;await page.getByRole('button',{name:'下一页',exact:true}).click();await page.getByRole('alert').filter({hasText:'翻页失败'}).waitFor();assert.ok((await page.locator('.page-summary').innerText()).includes('第 1 / 5 页'));assert.equal(await page.locator('.event-row').count(),10);
   failNext=false;await page.getByRole('button',{name:'下一页',exact:true}).click();await waitSummary('第 2 / 5 页');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`no overflow ${width}`);
   if(type==='contact_added'){await page.locator('.employee-filter').screenshot({path:`${out}/search-${width}.png`});await page.locator('.pagination').screenshot({path:`${out}/pagination-${width}.png`});}
   await page.screenshot({path:`${out}/${type}-${width}.png`,fullPage:true});
   await page.getByLabel('每页记录数').selectOption('20');await waitSummary('第 1 / 3 页');
   assert.ok(requests.some(q=>q.event_type===type&&q.employee_keyword==='张三'));
   console.log(`PASS mock ${type} ${width}: IME/ID/direct search, exact employee, clear, 3 pages back/forward, size reset, failed-page retention`);
  }
 }
 for(const width of [375,390,393,428,760,900,1440]){
  await page.setViewportSize({width,height:850});await page.waitForTimeout(100);
  const dock=await page.locator('.pagination').boundingBox();
  const navBox=await page.locator('.mobile-nav').boundingBox();
  assert.ok(Math.abs(dock.y+dock.height-(width<=760?navBox.y:850))<=1,`dock bottom ${width}`);
  await page.evaluate(()=>window.scrollTo(0,0));
  assert.ok(await page.getByRole('button',{name:'下一页',exact:true}).isVisible(),'pager visible without scrolling to list bottom');
  const initialY=(await page.locator('.pagination').boundingBox()).y;
  await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));await page.waitForTimeout(50);
  assert.ok(Math.abs((await page.locator('.pagination').boundingBox()).y-initialY)<=1,'pager stays docked while scrolling');
  const last=await page.locator('.event-row').last().boundingBox();
  assert.ok(last.y+last.height<=dock.y+1,`last record not hidden ${width}`);
  const footer=await page.locator('.page-footer').boundingBox();assert.ok(footer.y+footer.height<=dock.y+1,`footer not hidden ${width}`);
  const boxes=await Promise.all([page.getByLabel('每页记录数').boundingBox(),page.getByRole('button',{name:'上一页',exact:true}).boundingBox(),page.getByRole('button',{name:'下一页',exact:true}).boundingBox()]);
  assert.ok(boxes.every(Boolean));
  assert.ok(Math.max(...boxes.map(b=>b.y))-Math.min(...boxes.map(b=>b.y))<=1,`pagination controls align at ${width}`);
  assert.ok(boxes.every(b=>Math.abs(b.height-44)<=1),`pagination control heights ${width}`);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`pagination overflow ${width}`);
  if(width===393||width===1440){await page.locator('.pagination').screenshot({path:`${out}/pagination-docked-${width}.png`});await page.screenshot({path:`${out}/pagination-docked-viewport-${width}.png`});}
 }
 await page.setViewportSize({width:393,height:500});
 await page.locator('.mobile-nav').evaluate(el=>el.style.paddingBottom='48px');await page.waitForTimeout(120);
 const dockShort=await page.locator('.pagination').boundingBox(),navTall=await page.locator('.mobile-nav').boundingBox();
 assert.ok(Math.abs(dockShort.y+dockShort.height-navTall.y)<=1,'dock tracks increased nav height in short viewport');
 await page.locator('.mobile-nav').evaluate(el=>el.style.paddingBottom='');await page.setViewportSize({width:393,height:850});await page.waitForTimeout(100);
 await page.locator('.event-row').first().click();await page.getByRole('dialog').waitFor();
 const nextRect=await page.getByRole('button',{name:'下一页',exact:true}).boundingBox();
 assert.ok(await page.evaluate(b=>!!document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)?.closest('.modal-backdrop'),nextRect),'modal overlay covers dock');
 await page.getByRole('button',{name:'关闭',exact:true}).click();assert.ok(await page.locator('.pagination').isVisible());
 console.log('PASS docked pagination at seven widths: 44px aligned controls, above mobile nav, visible before/after scroll, final record unobscured');
 // Do not let a successful old page overwrite a newer employee search.
 await page.locator('.mobile-nav button').nth(1).click();await waitSummary('第 1 / 3 页');delayNext=true;
 await page.getByRole('button',{name:'下一页',exact:true}).click();
 for(let n=0;n<100&&!nextStarted;n++)await page.waitForTimeout(10);assert.ok(nextStarted);
 await page.getByLabel('搜索员工',{exact:true}).fill('不存在员工');await page.getByRole('button',{name:'搜索',exact:true}).click();await waitSummary('共 0 条');released();await page.waitForTimeout(150);
 assert.equal(await page.locator('.event-row').count(),0);assert.ok((await page.locator('.page-summary').innerText()).includes('第 1 / 1 页'));
 assert.deepEqual(errors,[]);console.log('PASS delayed previous-filter page success cannot overwrite current empty search');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
