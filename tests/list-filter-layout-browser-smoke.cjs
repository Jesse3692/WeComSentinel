// 仅使用 assistant_smoke_server.py 的临时合成数据，不读取本地业务库。
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin=process.env.ASSISTANT_SMOKE_URL || 'http://127.0.0.1:8870';
const out=process.env.UI_ARTIFACT_DIR || 'data/screenshots/list-filter-layout';

(async()=>{
  const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL || 'msedge'});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin);
    await page.getByLabel('演示角色').selectOption('user');
    await page.getByLabel('演示密码').fill('assistant-smoke-only-password');
    await page.getByRole('button',{name:'进入演示',exact:true}).click();
    await page.locator('.metric').first().waitFor();
    assert.ok((await page.locator('.page-footer').innerText()).includes('每 30 秒更新汇总'));
    fs.mkdirSync(out,{recursive:true});
    const ready=()=>page.waitForFunction(()=>!document.querySelector('.loading-status'));
    async function contained() {
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'no horizontal overflow');
      const bounds=await page.locator('.list-filters').boundingBox();
      const controls=await page.locator('.list-filters input,.list-filters select,.list-filters button').evaluateAll(els=>els.map(el=>{
        const r=el.getBoundingClientRect();return {left:r.left,right:r.right,height:r.height};
      }));
      assert.ok(controls.every(r=>r.left>=bounds.x && r.right<=bounds.x+bounds.width+1),'controls stay in filter card');
      const fields=await page.locator('.list-filters input,.list-filters select').evaluateAll(els=>els.map(el=>el.getBoundingClientRect().height));
      assert.ok(fields.every(h=>Math.abs(h-42)<=1),'consistent field heights');
      const input=page.getByLabel('搜索员工',{exact:true});
      if(await input.count()) {
        const boxes=await Promise.all([input.boundingBox(),page.getByRole('button',{name:'搜索',exact:true}).boundingBox(),page.getByRole('button',{name:'清空',exact:true}).boundingBox()]);
        assert.ok(Math.abs(boxes[1].y-boxes[2].y)<=1,'search and clear align');
        const contentWidth=await page.locator('.list-filters').evaluate(el=>el.clientWidth-parseFloat(getComputedStyle(el).paddingLeft)-parseFloat(getComputedStyle(el).paddingRight));
        if(contentWidth>550)assert.ok(Math.max(...boxes.map(b=>b.y))-Math.min(...boxes.map(b=>b.y))<=1,'desktop search controls align');
      }
    }
    async function checkColumns() {
      const list=page.locator('.contact-list');
      if(!await list.count())return;
      const contentWidth=await list.evaluate(el=>el.clientWidth-parseFloat(getComputedStyle(el).paddingLeft)-parseFloat(getComputedStyle(el).paddingRight));
      const header=list.locator('.event-columns');
      assert.equal(await header.isVisible(),contentWidth>=860,'column layout follows available list width');
      if(contentWidth<860 || !await list.locator('.event-row').count())return;
      const labels=[header.locator('.event-column-main>span').nth(0),header.locator('.event-column-main>span').nth(1),header.locator(':scope>span').nth(1)];
      const row=list.locator('.event-row').first();
      const fields=[row.locator('.event-content>strong'),row.locator('.employee-identity'),row.locator('time')];
      for(let i=0;i<labels.length;i++) {
        const label=await labels[i].boundingBox(), field=await fields[i].boundingBox();
        assert.ok(Math.abs(label.x-field.x)<=1,'header aligns with data column '+i);
      }
      const box=await header.boundingBox();
      const typography=await header.evaluate(el=>({size:parseFloat(getComputedStyle(el).fontSize),weight:Number(getComputedStyle(el).fontWeight)}));
      assert.ok(box.height>=52 && typography.size===15 && typography.weight>=600,'readable header typography and spacing');
    }
    for(const width of [3440,1920,1440,1101,1024,760,390,320]) {
      await page.setViewportSize({width,height:1000});
      const nav=page.locator(width>760?'.sidebar nav button':'.mobile-nav button');
      for(const index of [1,2,3]) {
        await nav.nth(index).click();await page.locator('.list-summary').waitFor();await ready();
        await contained();await checkColumns();
        assert.equal(await page.locator('.filters .detail-toolbar').count(),1);
        assert.ok(!(await page.locator('.page-footer').innerText()).includes('更新汇总'));
        await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
        const footer=await page.locator('.page-footer').boundingBox(), pager=await page.locator('.pagination').boundingBox();
        assert.ok(footer.y+footer.height<=pager.y+1,'footer above dock');
        assert.ok(pager.y-footer.y-footer.height<=20,'footer stays near pagination');
        const last=await page.locator('.event-row').last().boundingBox();
        if(last)assert.ok(last.y+last.height<=pager.y+1,'last event not covered');
        if([3440,1440,390,320].includes(width) && (index===1 || width===390)) {
          await page.evaluate(()=>window.scrollTo(0,0));
          await page.screenshot({path:path.join(out,`list-${index}-${width}.png`),fullPage:true});
          if(index===1)await page.locator('.list-filters').screenshot({path:path.join(out,`filters-${width}.png`)});
          if(index===1) {
            // 先滚至底部，避免截图时吸底分页覆盖手机列表（元素仍在可视区不代表未被浮层遮挡）。
            await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
            await page.locator('.event-list').screenshot({path:path.join(out,`event-list-${width}.png`)});
          }
        }
      }
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('.sidebar nav button').nth(1).click();await ready();
    const search=page.getByLabel('搜索员工',{exact:true});
    await search.fill('找不到的模拟员工');await search.press('Enter');
    await page.locator('.event-list .empty').waitFor();await ready();
    assert.equal(await page.locator('.event-row').count(),0);
    await page.getByRole('button',{name:'清空',exact:true}).click();await page.locator('.event-row').first().waitFor();await ready();
    await page.getByLabel('指定员工',{exact:true}).selectOption(JSON.stringify([1,'smoke-employee']));await ready();
    assert.equal(await page.locator('.event-row').count(),1);
    await page.getByRole('button',{name:'✦ AI 助手',exact:true}).click();
    for(const width of [1920,1440,1101]) {await page.setViewportSize({width,height:1000});await contained();await checkColumns();}
    await page.locator('.assistant-panel').getByRole('button',{name:'关闭助手',exact:true}).click();
    await page.getByRole('button',{name:'调整日期 →',exact:true}).click();
    await page.getByLabel('开始日期',{exact:true}).waitFor();
    assert.equal(await page.locator('input[type=date]').count(),2);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    console.log('PASS: list filters at 320–3440px, control alignment/containment, three list types, footer/pagination, direct search/clear/employee and assistant-narrow layout');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
