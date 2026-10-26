// Run only against assistant_smoke_server.py: isolated synthetic data, no platform/model calls.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.ASSISTANT_SMOKE_URL || 'http://127.0.0.1:8765';

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'msedge' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.getByLabel('演示角色').selectOption('user');
    await page.getByLabel('演示密码').fill('assistant-smoke-only-password');
    await page.getByRole('button', { name: '进入演示', exact: true }).click();
    await page.locator('.metric').first().waitFor();
    await page.getByRole('button', { name: '✦ AI 助手', exact: true }).click();
    const panel = page.locator('.assistant-panel');
    async function send(message) {
      const count = await panel.locator('.assistant-answer').count();
      await panel.getByLabel('向助手提问', { exact: true }).fill(message);
      await panel.getByRole('button', { name: '发送', exact: true }).click();
      await panel.locator('.assistant-answer').nth(count).waitFor();
      return panel.locator('.assistant-answer').last();
    }
    async function choose(capability) {
      const count = await panel.locator('.assistant-answer').count();
      await panel.getByLabel('助手查询能力').selectOption(capability);
      await panel.getByRole('button', { name: '查询所选', exact: true }).click();
      await panel.locator('.assistant-answer').nth(count).waitFor();
      return panel.locator('.assistant-answer').last();
    }
    await send('生成当前区间周报');
    const summary = panel.locator('.assistant-charts').first();
    await summary.locator('.chart-canvas svg').first().waitFor();
    assert.equal(await summary.locator('.assistant-chart').count(), 2);
    await summary.locator('.chart-values summary').first().click();
    assert.deepEqual(await summary.locator('table').first().locator('tbody tr').allTextContents(), ['新增联系人01', '删除联系人02', '主动退群03', '被移出群04']);
    await summary.locator('.chart-values summary').nth(1).click();
    assert.deepEqual(await summary.locator('table').nth(1).locator('tbody tr').allTextContents(), ['删除关系200']);
    await summary.locator('.chart-values summary').first().click();
    await summary.locator('.chart-values summary').nth(1).click();
    for (const width of [1440, 1101, 1024, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.waitForTimeout(150);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `document overflow at ${width}`);
      assert.equal(await panel.locator('.assistant-log').evaluate(el => el.scrollWidth > el.clientWidth), false, `chat overflow at ${width}`);
      for (const canvas of await summary.locator('.chart-canvas').all()) {
        const box = await canvas.boundingBox();
        assert.ok(box.width >= 240 && box.width < width, `chart width at ${width}: ${box.width}`);
        const svg = await canvas.locator('svg').boundingBox();
        assert.ok(Math.abs(svg.width - box.width) <= 1, 'SVG resizes with panel');
      }
      if (process.env.ASSISTANT_SCREENSHOT_DIR && [1440, 320].includes(width)) {
        fs.mkdirSync(process.env.ASSISTANT_SCREENSHOT_DIR, { recursive: true });
        await summary.evaluate(el => {
          const log = el.closest('.assistant-log');
          log.scrollTop += el.getBoundingClientRect().top - log.getBoundingClientRect().top;
        });
        await page.screenshot({ path: path.join(process.env.ASSISTANT_SCREENSHOT_DIR, `assistant-charts-${width}.png`) });
      }
    }
    await summary.getByRole('button', { name: '收起图表', exact: true }).click();
    assert.equal(await summary.locator('.chart-canvas').count(), 0);
    await summary.getByRole('button', { name: '展开图表', exact: true }).click();
    await summary.locator('.chart-canvas svg').first().waitFor();
    await summary.getByRole('button', { name: '查看每日趋势图', exact: true }).click();
    const trend = panel.locator('.assistant-answer').last();
    await trend.locator('[data-chart-kind="trend"] svg').waitFor();
    assert.equal(await trend.locator('.chart-legend span').count(), 5);
    assert.match(await trend.locator('.chart-note').last().innerText(), /当前页.*天/);
    const stats = await choose('statistics');
    await stats.locator('[data-chart-kind="comparison"] svg').waitFor();
    assert.equal(await stats.locator('.chart-values tbody tr').count(), 5);
    const ranks = await choose('rankings');
    await ranks.locator('[data-chart-kind="ranking"] svg').waitFor();
    assert.match(await ranks.locator('.chart-note').last().innerText(), /当前页前/);
    const customers = await choose('customers');
    assert.equal(await customers.locator('.assistant-charts').count(), 0);
    await send('查看事件 #1');
    assert.equal(await panel.locator('.assistant-answer').last().locator('.assistant-charts').count(), 0);

    // Simulate model prose independently of real API facts; chart data must stay unchanged.
    await page.route('**/api/v1/assistant/chat', async route => {
      const upstream = await route.fetch();
      const payload = await upstream.json();
      payload.data.mode = 'model';
      payload.data.answer = '模型模拟文字：新增99999人次';
      await route.fulfill({ response: upstream, json: payload });
    });
    const modeled = await send('生成当前区间周报');
    await modeled.locator('.chart-canvas svg').first().waitFor();
    assert.match(await modeled.locator('.assistant-answer-text').innerText(), /99999/);
    assert.equal(await modeled.locator('.chart-values table').first().locator('tbody tr').first().textContent(), '新增联系人01');
    assert.ok(!(await modeled.locator('.assistant-charts').innerText()).includes('99999'));
    await page.unroute('**/api/v1/assistant/chat');

    await page.route('**/api/v1/assistant/chat', async route => {
      const upstream = await route.fetch();
      const payload = await upstream.json();
      payload.data.cards = payload.data.cards.map(card => ({ ...card, value: 0, delta: 0 }));
      payload.data.deletion_directions = { customer_deleted_employee: 0, employee_deleted_customer: 0, unknown: 0 };
      await route.fulfill({ response: upstream, json: payload });
    });
    const zero = await send('生成当前区间周报');
    assert.equal(await zero.locator('.chart-empty').count(), 2);
    assert.equal(await zero.locator('.chart-canvas svg').count(), 0);
    await page.unroute('**/api/v1/assistant/chat');
    await panel.getByRole('button', { name: '新对话', exact: true }).click();
    assert.equal(await panel.locator('.assistant-chart').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: assistant weekly/analysis charts, real API quantities, direction composition, 320–1440px resize, collapse/recreate, related daily trend, statistics/rankings, no list/event charts, model prose isolation, all-zero state and cleanup');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
