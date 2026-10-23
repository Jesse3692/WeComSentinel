const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const base='http://127.0.0.1:8011';await page.goto(base);
assert.equal((await page.request.get(base+'/api/v1/media/1')).status(),401);
await page.getByLabel('演示角色').selectOption('user');await page.getByLabel('演示密码').fill('local-test-password');await page.getByRole('button',{name:'进入演示',exact:true}).click();await page.locator('.metric').first().waitFor();
assert.equal((await page.request.get(base+'/api/v1/media/2')).status(),404);
const response=await page.request.get(base+'/api/v1/media/1');assert.equal(response.status(),200);assert.equal(response.headers()['content-type'],'image/png');
await page.locator('.sidebar nav button').nth(3).click();await page.locator('.member-avatars img').waitFor();
await page.locator('.member-avatars img').evaluate(async el=>{await el.decode();if(!el.complete || el.naturalWidth!==1 || el.naturalHeight!==1)throw new Error('image not decoded')});
await page.locator('.event-row').click();await page.getByRole('dialog').locator('.avatar img').evaluate(async el=>{await el.decode();if(!el.complete || el.naturalWidth!==1)throw new Error('detail image not decoded')});
assert.deepEqual(errors,[]);console.log('Real local media endpoint passed: unauthenticated401, unauthorized404, authorizedPNG200, list/detail decoded images');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
