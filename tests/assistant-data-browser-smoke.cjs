// Use only the isolated synthetic fixture in assistant_smoke_server.py.
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin=process.env.ASSISTANT_SMOKE_URL || 'http://127.0.0.1:8766';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL || 'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin);
  async function login(role){await page.getByLabel('演示角色').selectOption(role);await page.getByLabel('演示密码').fill('assistant-smoke-only-password');await page.getByRole('button',{name:'进入演示',exact:true}).click();await page.locator('.metric').first().waitFor();await page.getByRole('button',{name:'✦ AI 助手',exact:true}).click();}
  await login('user');
  const panel=page.locator('.assistant-panel');
  await panel.getByLabel('助手查询能力').locator('option[value="customers"]').waitFor({state:'attached'});
  assert.equal(await panel.getByLabel('助手查询能力').locator('option[value="jobs"]').count(),0);
  let expected=0;
  async function choose(capability){await panel.getByLabel('助手查询能力').selectOption(capability);await panel.getByRole('button',{name:'查询所选',exact:true}).click();expected++;await page.waitForFunction(n=>document.querySelectorAll('.assistant-dataset').length===n,expected);return panel.locator('.assistant-dataset').last();}
  let data=await choose('employees');assert.ok((await data.innerText()).includes('模拟员工'));
  data=await choose('customers');assert.ok((await data.innerText()).includes('模拟客户'));
  await data.getByRole('button',{name:'跟进员工 →',exact:true}).click();expected++;await page.waitForFunction(n=>document.querySelectorAll('.assistant-dataset').length===n,expected);
  assert.ok((await panel.locator('.assistant-dataset').last().innerText()).includes('模拟员工'));
  await page.setViewportSize({width:390,height:844});
  data=await choose('group_members');
  await data.getByRole('button',{name:'群成员 →',exact:true}).first().click();expected++;await page.waitForFunction(n=>document.querySelectorAll('.assistant-dataset').length===n,expected);
  const members=panel.locator('.assistant-dataset').last();assert.equal(await members.locator('.data-record').count(),2);
  assert.ok((await members.innerText()).includes('内部员工'));assert.ok((await members.innerText()).includes('外部联系人'));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  data=await choose('events');assert.equal(await data.locator('.data-record').count(),4);
  await data.getByRole('button',{name:'事件详情 →',exact:true}).first().click();await page.getByRole('dialog',{name:'事件详情',exact:true}).waitFor();
  await page.getByRole('button',{name:'关闭',exact:true}).click();await page.getByRole('button',{name:'✦ AI 助手',exact:true}).click();
  data=await choose('trend');assert.ok((await data.innerText()).includes('每日事件趋势'));
  data=await choose('me');assert.ok((await data.innerText()).includes('冒烟普通用户'));
  const forbidden=await page.evaluate(async()=>{const me=(await(await fetch('/api/v1/me')).json()).data;return(await fetch('/api/v1/assistant/chat',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':me.csrf_token},body:JSON.stringify({message:'查询',query:{capability:'jobs'}})})).status;});
  assert.equal(forbidden,403);
  await panel.getByRole('button',{name:'关闭助手',exact:true}).click();await page.locator('.mobile-nav button').nth(4).click();await page.locator('.profile').getByRole('button',{name:'退出登录',exact:true}).click();
  await login('admin');expected=0;
  await panel.getByLabel('助手查询能力').locator('option[value="jobs"]').waitFor({state:'attached'});
  for(const capability of ['jobs','callbacks','users','audits','tenant_settings','capabilities']){data=await choose(capability);assert.ok(await data.locator('.data-record').count()>0);}
  assert.ok((await panel.getByLabel('助手查询能力').innerText()).includes('管理员'));
  assert.deepEqual(errors,[]);
  console.log('PASS: data capability picker, employees/customers/followers, group members, event links, trends, account, admin metadata and role isolation');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
