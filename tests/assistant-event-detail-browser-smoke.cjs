// 仅对 assistant_smoke_server.py 的独立临时合成数据库运行，不访问真实平台。
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.ASSISTANT_SMOKE_URL || 'http://127.0.0.1:8767';

(async()=>{
  const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL || 'msedge'});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(origin);
    await page.getByLabel('演示角色').selectOption('user');
    await page.getByLabel('演示密码').fill('assistant-smoke-only-password');
    await page.getByRole('button',{name:'进入演示',exact:true}).click();
    await page.locator('.metric').first().waitFor();
    await page.getByRole('button',{name:'✦ AI 助手',exact:true}).click();
    const panel=page.locator('.assistant-panel'), log=panel.locator('.assistant-log');
    const input=panel.getByLabel('向助手提问',{exact:true});
    const dialog=page.getByRole('dialog',{name:'事件详情',exact:true});
    await panel.getByLabel('助手查询能力').selectOption('events');
    await panel.getByRole('button',{name:'查询所选',exact:true}).click();
    const eventLink=panel.getByRole('button',{name:'事件详情 →',exact:true}).first();
    await eventLink.waitFor();
    const originalText=await panel.locator('.assistant-exchange').innerText();
    const originalOverflow=await page.evaluate(()=>document.body.style.overflow);
    let eventId;

    // 同一对话遍历断点，验证详情位于助手上层，且不同关闭方式只关闭顶层。
    for(const [width,close] of [[1440,'backdrop'],[1024,'escape'],[390,'return'],[320,'close']]) {
      await page.setViewportSize({width,height:844});
      await input.fill('尚未发送的输入');
      await eventLink.scrollIntoViewIfNeeded();await eventLink.focus();
      const beforeScroll=await log.evaluate(el=>el.scrollTop);
      await eventLink.click();await dialog.waitFor();
      assert.equal(await panel.count(),1);
      assert.equal(await panel.getAttribute('inert'),'');
      assert.equal(await panel.getAttribute('aria-modal'),null);
      assert.equal(await page.evaluate(()=>document.body.style.overflow),'hidden');
      assert.ok(await dialog.evaluate(el=>{
        const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+30));
      }),'detail must be above the assistant');
      if(width<=760)assert.equal(Math.round((await dialog.boundingBox()).width),width);
      if(process.env.ASSISTANT_SCREENSHOT_DIR && [1440,390].includes(width)) {
        require('node:fs').mkdirSync(process.env.ASSISTANT_SCREENSHOT_DIR,{recursive:true});
        await page.screenshot({path:require('node:path').join(process.env.ASSISTANT_SCREENSHOT_DIR,`event-detail-${width}.png`)});
      }
      if(close==='backdrop')await page.locator('.modal-backdrop').click({position:{x:4,y:4}});
      else if(close==='escape')await page.keyboard.press('Escape');
      else if(close==='return')await dialog.getByRole('button',{name:'← 返回助手',exact:true}).click();
      else await dialog.getByRole('button',{name:'关闭',exact:true}).click();
      await dialog.waitFor({state:'hidden'});
      assert.equal(await panel.getAttribute('inert'),null);
      assert.equal(await input.inputValue(),'尚未发送的输入');
      assert.equal(await panel.locator('.assistant-exchange').innerText(),originalText);
      assert.ok(Math.abs(await log.evaluate(el=>el.scrollTop)-beforeScroll)<=2,'restore conversation scroll');
      assert.ok(await eventLink.evaluate(el=>el===document.activeElement),'restore event trigger focus');
      assert.equal(await page.evaluate(()=>document.body.style.overflow),width<=1100?'hidden':originalOverflow);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    }

    // 查看事件依据也保留助手；追加解释指定同一个事件，且不清空输入与历史。
    await page.setViewportSize({width:1440,height:1000});
    await panel.getByRole('button',{name:'对比当前区间的客户变化',exact:true}).click();
    const source=panel.locator('.assistant-sources button').first();await source.waitFor();
    await input.fill('尚未发送的输入');
    await source.click();await dialog.waitFor();
    await dialog.getByRole('button',{name:'关闭',exact:true}).click();
    const beforeCount=await panel.locator('.assistant-exchange').count();
    await eventLink.click();await dialog.waitFor();
    const chatRequest=page.waitForRequest(req=>req.url().endsWith('/assistant/chat') && req.method()==='POST');
    await dialog.getByRole('button',{name:'✦ 让助手解释',exact:true}).click();
    const body=(await chatRequest).postDataJSON();eventId=body.event_id;
    assert.ok(eventId>0);assert.equal(body.tenant_id,'1');assert.equal(body.query,null);
    await panel.locator('.assistant-exchange').nth(beforeCount).locator('.assistant-answer').waitFor();
    assert.equal(await panel.locator('.assistant-exchange').count(),beforeCount+1);
    assert.equal(await input.inputValue(),'尚未发送的输入');

    // 404 在助手内重试，不把单个事件失效解释为整个助手关闭。
    await page.route('**/api/v1/events/*',route=>route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({error:{message:'事件已失效'}})}));
    await eventLink.click();await panel.getByRole('alert').filter({hasText:'事件已失效'}).waitFor();
    assert.equal(await dialog.count(),0);assert.equal(await panel.count(),1);
    assert.equal(await input.inputValue(),'尚未发送的输入');
    await page.unroute('**/api/v1/events/*');
    await panel.getByRole('button',{name:'重试事件详情',exact:true}).click();await dialog.waitFor();

    // 断点切换不能提前释放详情的滚动锁；最后一个浮层关闭后才解锁。
    await page.setViewportSize({width:390,height:844});
    await page.setViewportSize({width:1440,height:1000});
    assert.equal(await page.evaluate(()=>document.body.style.overflow),'hidden');
    await dialog.getByRole('button',{name:'关闭',exact:true}).click();
    assert.equal(await page.evaluate(()=>document.body.style.overflow),originalOverflow);

    // 显式事件解释失败后的重试必须保留事件编号和主体，不能退化为区间分析。
    await page.route('**/api/v1/assistant/chat',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{message:'模拟分析失败'}})}));
    await eventLink.click();await dialog.waitFor();
    await dialog.getByRole('button',{name:'✦ 让助手解释',exact:true}).click();
    const failed=panel.locator('.assistant-exchange').last();await failed.getByRole('alert').waitFor();
    await page.unroute('**/api/v1/assistant/chat');
    const retryRequest=page.waitForRequest(req=>req.url().endsWith('/assistant/chat') && req.method()==='POST');
    await failed.getByRole('button',{name:'重试',exact:true}).click();
    const retryBody=(await retryRequest).postDataJSON();
    assert.equal(retryBody.event_id,eventId);assert.equal(retryBody.tenant_id,'1');
    await panel.locator('.assistant-exchange').last().locator('.assistant-answer').waitFor();
    assert.equal(await input.inputValue(),'尚未发送的输入');

    // 生成期间仍可查看已有依据，但不能再发起解释；后台回答不应滚动上层详情。
    let releaseChat, chatStarted;
    const chatGate=new Promise(resolve=>releaseChat=resolve), chatReady=new Promise(resolve=>chatStarted=resolve);
    await page.route('**/api/v1/assistant/chat',async route=>{
      const response=await route.fetch();chatStarted();await chatGate;
      await route.fulfill({response}).catch(()=>{});
    });
    await panel.getByRole('button',{name:'对比当前区间的客户变化',exact:true}).click();await chatReady;
    await source.click();await dialog.waitFor();
    assert.equal(await dialog.getByRole('button',{name:'✦ 让助手解释',exact:true}).isDisabled(),true);
    const suspendedScroll=await log.evaluate(el=>el.scrollTop);
    releaseChat();await page.unrouteAll({behavior:'wait'});
    await dialog.getByRole('button',{name:'✦ 让助手解释',exact:true}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('.event-assistant-entry')?.disabled);
    assert.equal(await log.evaluate(el=>el.scrollTop),suspendedScroll);
    await dialog.getByRole('button',{name:'关闭',exact:true}).click();
    await input.fill('尚未发送的输入');

    // 请求未完成时关闭助手，迟到结果不能重新弹出详情。
    let release, started;
    const gate=new Promise(resolve=>release=resolve), ready=new Promise(resolve=>started=resolve);
    await page.route('**/api/v1/events/*',async route=>{
      const response=await route.fetch();started();await gate;
      await route.fulfill({response}).catch(()=>{});
    });
    await eventLink.click();await ready;
    await panel.getByRole('button',{name:'关闭助手',exact:true}).click();
    release();await page.unrouteAll({behavior:'wait'});
    assert.equal(await dialog.count(),0);assert.equal(await panel.count(),0);
    assert.equal(await page.evaluate(()=>document.body.style.overflow),originalOverflow);
    await page.getByRole('button',{name:'✦ AI 助手',exact:true}).click();
    await panel.waitFor();assert.equal(await dialog.count(),0);
    assert.equal(await input.inputValue(),'尚未发送的输入');
    await page.setViewportSize({width:390,height:844});
    await panel.getByRole('button',{name:'关闭助手',exact:true}).click();
    assert.equal(await page.evaluate(()=>document.body.style.overflow),originalOverflow);

    // 切换主体及会话失效也必须丢弃未完成的详情，不能在新上下文或登录页弹出旧资料。
    await page.setViewportSize({width:1440,height:1000});
    await page.getByRole('button',{name:'✦ AI 助手',exact:true}).click();
    async function holdEvent() {
      let finish, begin;
      const held=new Promise(resolve=>finish=resolve), began=new Promise(resolve=>begin=resolve);
      await page.route('**/api/v1/events/*',async route=>{
        const response=await route.fetch();begin();await held;
        await route.fulfill({response}).catch(()=>{});
      });
      await eventLink.click();await began;
      return async()=>{finish();await page.unrouteAll({behavior:'wait'});};
    }
    const finishScope=await holdEvent();
    const tenantPicker=page.locator('.filters select').first();
    await tenantPicker.selectOption((await tenantPicker.inputValue())==='all'?'1':'all');
    await finishScope();
    assert.equal(await dialog.count(),0);
    assert.equal(await panel.locator('.assistant-exchange').count(),0);
    await panel.getByLabel('助手查询能力').selectOption('events');
    await panel.getByRole('button',{name:'查询所选',exact:true}).click();await eventLink.waitFor();
    const finishSession=await holdEvent();
    await page.evaluate(()=>window.dispatchEvent(new Event('session-expired')));
    await page.getByRole('button',{name:'进入演示',exact:true}).waitFor();
    await finishSession();
    assert.equal(await dialog.count(),0);assert.equal(await panel.count(),0);
    assert.equal(await page.evaluate(()=>document.body.style.overflow),originalOverflow);
    assert.deepEqual(errors,[]);
    console.log('PASS: assistant event detail desktop/tablet/mobile, layering, close/return/focus, draft/history/scroll, explanation/retry, 404, busy generation, late response/scope/session and nested scroll locks');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
