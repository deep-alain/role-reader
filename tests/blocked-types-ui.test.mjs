import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
const source=await fs.readFile(new URL('../extension/app.js',import.meta.url),'utf8');
const helper=source.slice(source.indexOf('function supportsTypeExclusion('),source.indexOf('function applyPool('));
const handler=source.slice(source.indexOf('async function feedbackBot('),source.indexOf("$('undoFeedback').onclick="));
function fixture(request){
 const messages=[],requests=[],refreshes=[],undo={hidden:true},learning={blockedTypes:[]};
 const context=vm.createContext({libraryView:'pool',lastFeedback:null,feedbackSubmitting:new Set(),poolState:{revision:1,preferences:{learning}},
 poolRequest:async(path,body)=>{requests.push({path,...body});return request?request(path,body):{pool:{revision:2,preferences:{learning}}}},
 applyPool(next){if(next.revision>=context.poolState.revision)context.poolState={...context.poolState,...next}},refreshBatchResults:async()=>{},
 poolRefreshView:{request:value=>refreshes.push(value)},$:id=>id==='undoFeedback'?undo:null,actionNotice:value=>messages.push(value)});
 vm.runInContext(helper+handler,context);return {context,messages,requests,refreshes,undo};
}
test('feedback immediately refreshes and gives a short visible confirmation with undo',async()=>{
 const {context,messages,requests,refreshes,undo}=fixture();
 await context.feedbackBot('role-1','less');assert.equal(requests[0].choice,'less');assert.equal(context.lastFeedback,'role-1');assert.equal(undo.hidden,false);assert.equal(messages.at(-1),'已排除此类');
 assert.ok(refreshes.every(value=>value.immediate));
 await context.feedbackBot('role-1','clear');assert.equal(context.lastFeedback,null);assert.equal(undo.hidden,true);assert.equal(messages.at(-1),'已撤销偏好');
});
test('old backend cannot receive a type-exclusion vote until restart',async()=>{
 const {context,messages,requests}=fixture();context.poolState={preferences:{learning:{uniqueExamples:3}}};
 await context.feedbackBot('role-1','less');assert.equal(requests.length,0);assert.match(messages.at(-1),/未提交/);
});
test('repeated clicks share one pending request and the busy state clears',async()=>{
 let resolve;const pending=new Promise(done=>resolve=done),{context,requests}=fixture(()=>pending);
 const first=context.feedbackBot('role','less');await context.feedbackBot('role','less');
 assert.equal(requests.length,1);assert.equal(context.feedbackSubmitting.has('role'),true);
 resolve({pool:{revision:2}});await first;assert.equal(context.feedbackSubmitting.size,0);
});
test('failed feedback remains retryable and does not claim a successful exclusion',async()=>{
 const {context,messages,requests}=fixture(()=>{throw new Error('offline')});
 await context.feedbackBot('role','less');assert.equal(context.feedbackSubmitting.size,0);assert.equal(messages.at(-1),'保存失败：offline');
 await context.feedbackBot('role','less');assert.equal(requests.length,2);assert.equal(context.lastFeedback,null);
});
test('a late older feedback response cannot overwrite the latest undo target',async()=>{
 const {context}=fixture(()=>({pool:{revision:2,lastFeedback:{id:'old'}}}));context.poolState.revision=3;context.lastFeedback='new';
 await context.feedbackBot('old','less');assert.equal(context.lastFeedback,'new');
});
