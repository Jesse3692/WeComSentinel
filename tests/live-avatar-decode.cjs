const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{let input='';for await(const chunk of process.stdin)input+=chunk;const items=JSON.parse(input);assert.ok(items.length>0);
const browser=await chromium.launch({headless:true,channel:'msedge'});try{
 const page=await browser.newPage();await page.setContent('<html><body><img id="avatar"></body></html>');const results=[];
 for(const item of items){const result=await page.locator('#avatar').evaluate(async(img,{mime,base64})=>{img.src='data:'+mime+';base64,'+base64;await img.decode();return {complete:img.complete,width:img.naturalWidth,height:img.naturalHeight}},{mime:item.mime,base64:item.base64});assert.ok(result.complete&&result.width>1&&result.height>1);results.push({media_id:item.media_id,...result})}
 console.log(JSON.stringify({source:'actual production media files, no mock images',decoded:results}));
}finally{await browser.close()}})().catch(()=>{console.error('Actual production image decode failed');process.exit(1)});
