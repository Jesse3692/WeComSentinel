const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const code = ts.transpileModule(fs.readFileSync('frontend/src/api.ts','utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
(async()=>{
  let resolve, notifications=0;
  const context={exports:{},DOMException,CustomEvent:class{},window:{dispatchEvent(){notifications++}},fetch:()=>new Promise(r=>resolve=r)};
  vm.runInNewContext(code,context);const {api,invalidateSession}=context.exports;
  const old=api('/events');invalidateSession();resolve({ok:false,status:401,json:async()=>({error:{message:'expired'}})});
  await assert.rejects(old,e=>e.name==='AbortError');assert.equal(notifications,0);
  const logout=api('/auth/logout',{},'POST',false);resolve({ok:false,status:401,json:async()=>({})});
  await assert.rejects(logout,e=>e.status===401);assert.equal(notifications,0);
  const active=api('/events');resolve({ok:false,status:401,json:async()=>({})});
  await assert.rejects(active,e=>e.status===401);assert.equal(notifications,1);
  const oldSuccess=api('/me');invalidateSession();resolve({ok:true,json:async()=>({data:{name:'old'}})});
  await assert.rejects(oldSuccess,e=>e.name==='AbortError');
  console.log('Request generation tests passed: stale success/401, logout 401, active expiry');
})().catch(e=>{console.error(e);process.exit(1)});
