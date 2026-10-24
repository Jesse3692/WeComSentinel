// Run only against tests/assistant_smoke_server.py (isolated synthetic database).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.ASSISTANT_SMOKE_URL || 'http://127.0.0.1:8765';

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
    const panel=page.locator('.assistant-panel');
    await panel.getByRole('button',{name:'对比当前区间的客户变化',exact:true}).click();
    await panel.locator('.assistant-answer').waitFor({timeout:65000});
    if(process.env.ASSISTANT_EXPECT_MODEL==='1')assert.equal(await panel.locator('.assistant-answer-label').innerText(),'模型分析');
    assert.equal(await panel.locator('.assistant-cards>div').count(),4);
    assert.equal(await panel.locator('.assistant-cards strong').nth(2).innerText(),'3 人次');
    const bounds=await panel.boundingBox(), main=await page.locator('.app-shell>main').boundingBox();
    assert.ok(main.x+main.width<=bounds.x+1,'desktop panel must not cover main');
    if(process.env.ASSISTANT_SCREENSHOT_DIR){fs.mkdirSync(process.env.ASSISTANT_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.ASSISTANT_SCREENSHOT_DIR,'assistant-implemented-desktop.png'),fullPage:true});}
    await panel.getByRole('button',{name:'生成当前区间周报',exact:true}).click();
    await panel.locator('.assistant-answer').nth(1).waitFor({timeout:65000});
    if(process.env.ASSISTANT_EXPECT_MODEL==='1')assert.equal(await panel.locator('.assistant-answer-label').nth(1).innerText(),'模型分析');
    assert.ok((await panel.locator('.assistant-answer-text').nth(1).innerText()).length>0);
    await panel.getByRole('button',{name:'当前主体中有哪些群',exact:true}).click();
    await panel.locator('.assistant-groups').first().waitFor();
    assert.equal(await panel.locator('.assistant-answer-label').nth(2).innerText(),'数据查询');
    assert.equal(await panel.locator('.assistant-groups li').count(),20);
    assert.ok(!(await panel.innerText()).includes('无权查看的群'));
    await panel.getByRole('button',{name:'下一页群列表',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.assistant-groups').length===2);
    const lastGroups=panel.locator('.assistant-groups').last();
    assert.equal(await lastGroups.locator('li').count(),5);
    await lastGroups.getByLabel('群名包含',{exact:true}).fill('群24');
    await lastGroups.getByRole('button',{name:'筛选',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.assistant-groups').length===3);
    assert.equal(await panel.locator('.assistant-groups').last().locator('li').count(),1);
    assert.ok((await panel.locator('.assistant-groups').last().innerText()).includes('冒烟客户群24'));
    const tenantPicker=page.locator('.filters select').first();
    await tenantPicker.selectOption((await tenantPicker.inputValue())==='all'?'1':'all');
    await page.waitForFunction(()=>document.querySelectorAll('.assistant-answer').length===0);
    await page.route('**/api/v1/assistant/chat',async route=>{
      await new Promise(resolve=>setTimeout(resolve,1000));
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:{answer:'late-answer-must-not-appear'}})}).catch(()=>{});
    });
    await panel.getByLabel('向助手提问',{exact:true}).fill('测试停止生成');
    await panel.getByRole('button',{name:'发送',exact:true}).click();
    await panel.getByRole('button',{name:'停止生成',exact:true}).click();
    await page.waitForTimeout(1200);
    assert.ok(!(await panel.innerText()).includes('late-answer-must-not-appear'));
    assert.ok((await panel.innerText()).includes('本次生成已停止'));
    await page.unroute('**/api/v1/assistant/chat');
    await panel.getByRole('button',{name:'新对话',exact:true}).click();
    for(const width of [1440,1101,1024,390,320]){
      await page.setViewportSize({width,height:844});
      await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth,{},{timeout:3000});
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
      if(overflow)console.log(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(el=>el.getBoundingClientRect().right>innerWidth+1).slice(0,12).map(el=>({tag:el.tagName,class:el.className,right:el.getBoundingClientRect().right,width:el.getBoundingClientRect().width}))));
      assert.equal(overflow,false,`overflow at ${width}`);
    }
    await page.setViewportSize({width:390,height:844});
    const mobile=await panel.boundingBox();
    assert.equal(Math.round(mobile.width),390);
    assert.ok(Math.abs(mobile.y)<2,'mobile assistant should start at the viewport top');
    assert.equal(await panel.getAttribute('role'),'dialog');
    await panel.getByRole('button',{name:'当前主体中有哪些群',exact:true}).click();
    await panel.locator('.assistant-groups').waitFor();
    assert.equal(await panel.locator('.assistant-groups li').count(),20);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await panel.getByRole('button',{name:'关闭助手',exact:true}).click();
    await page.locator('.mobile-nav button').nth(3).click();
    await page.locator('.event-row').first().click();
    await page.getByRole('button',{name:'✦ 让助手解释',exact:true}).click();
    await panel.locator('.assistant-answer').waitFor({timeout:65000});
    if(process.env.ASSISTANT_EXPECT_MODEL==='1')assert.equal(await panel.locator('.assistant-answer-label').innerText(),'模型分析');
    assert.ok((await panel.locator('.assistant-answer-text').innerText()).includes('推断'));
    if(process.env.ASSISTANT_SCREENSHOT_DIR){await panel.locator('.assistant-log').evaluate(el=>{el.scrollTop=0});await page.screenshot({path:path.join(process.env.ASSISTANT_SCREENSHOT_DIR,'assistant-implemented-mobile.png')});}
    await panel.locator('.assistant-sources button').first().click();
    await page.getByRole('dialog',{name:'事件详情',exact:true}).waitFor();
    assert.equal(await page.locator('.assistant-panel').count(),0);
    await page.getByRole('button',{name:'关闭',exact:true}).click();
    await page.getByRole('button',{name:'✦ AI 助手',exact:true}).click();
    await panel.getByRole('button',{name:'关闭助手',exact:true}).click();
    await page.locator('.mobile-nav button').nth(4).click();
    await page.locator('.profile').getByRole('button',{name:'退出登录',exact:true}).click();
    await page.getByRole('button',{name:'进入演示',exact:true}).waitFor();
    assert.equal(await page.locator('.assistant-panel').count(),0);
    assert.deepEqual(errors,[]);
    console.log('PASS: assistant desktop/mobile 320–1440px, group list/pagination/filter/permissions, stats, report, event explanation, scope reset, cancellation, detail links and logout');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
