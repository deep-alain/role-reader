import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

const source=await fs.readFile(new URL('../extension/app.js',import.meta.url),'utf8');
const helpers=source.slice(source.indexOf('function supportsCoverFeedback('),source.indexOf('function applyPool('));
const handler=source.slice(source.indexOf('async function coverFeedbackBot('),source.indexOf("$('undoFeedback').onclick="));
const approved={id:'lindsey',name:'Lindsey',avatarAsset:{review:'approved',sha256:'avatar-sha'}};

function fixture({enabled=true,record=approved,libraryView='pool',request}={}){
 const requests=[],messages=[],applied=[],context=vm.createContext({
  poolState:{preferences:{cover:enabled?{enabled:true,references:[]}:undefined}},libraryView,lastFeedback:'existing-story-vote',coverSubmitting:new Set(),displayedRecords:()=>[record],poolRefreshView:{request(){}},
  poolRequest:async(path,body)=>{requests.push({path,...body});return request?request(path,body):{effect:body.choice==='clear'?'已撤销封面参考':'已保存封面参考',pool:{}}},
  applyPool:pool=>applied.push(pool),refreshBatchResults:async()=>{context.batchRefreshes++},batchRefreshes:0,message:value=>messages.push(value),
 });
 vm.runInContext(helpers+handler,context);
 return {context,requests,messages,applied};
}

test('cover votes use their independent endpoint, keep story undo, and refresh batch results',async()=>{
 const {context,requests,messages,applied}=fixture({libraryView:'batch'});
 await context.coverFeedbackBot('lindsey','more');await context.coverFeedbackBot('lindsey','clear');
 assert.deepEqual(requests,[{path:'/api/pool/cover-feedback',id:'lindsey',choice:'more'},{path:'/api/pool/cover-feedback',id:'lindsey',choice:'clear'}]);
 assert.equal(context.lastFeedback,'existing-story-vote');assert.equal(context.batchRefreshes,2);assert.equal(applied.length,2);
 assert.deepEqual(messages,['已保存封面参考','已撤销封面参考']);assert.equal(context.coverSubmitting.size,0);
 assert.ok(requests.every(value=>!['/api/pool/feedback','/api/pool/use'].includes(value.path)));
});

test('old backend leaves the cover button disabled and never receives a cover vote',async()=>{
 const {context,requests,messages}=fixture({enabled:false});
 const state=context.coverFeedbackState(approved);assert.equal(state.disabled,true);assert.match(state.title,/尚未生效.*重启/);
 await context.coverFeedbackBot('lindsey','more');assert.equal(requests.length,0);assert.match(messages[0],/尚未生效/);assert.match(messages[0],/没有提交/);
});

test('unapproved portraits cannot become cover references',async()=>{
 const record={...approved,avatarAsset:{...approved.avatarAsset,review:'pending'}},{context,requests,messages}=fixture({record});
 assert.equal(context.coverFeedbackState(record).disabled,true);assert.match(context.coverFeedbackState(record).title,/检查并通过来源头像/);
 await context.coverFeedbackBot('lindsey','more');assert.equal(requests.length,0);assert.match(messages[0],/没有提交/);
});

test('an exact reference portrait hash enables undo even when the record has no reference flag',()=>{
 const {context}=fixture();context.poolState.preferences.cover.references=[{name:'Lindsey',sha256:'avatar-sha'}];
 const referenced=context.coverFeedbackState(approved);assert.equal(referenced.choice,'clear');assert.equal(referenced.label,'撤销封面参考');assert.equal(referenced.disabled,false);
 const unrelated=context.coverFeedbackState({...approved,avatarAsset:{...approved.avatarAsset,sha256:'another-avatar'}});assert.equal(unrelated.choice,'more');assert.equal(unrelated.label,'封面多来');
});

test('actual pool summary lists cover references and preserves all names in its hover text',()=>{
 const elements=new Map(),names=['Lindsey','Another reference','Third reference','Fourth reference'];
 const context=vm.createContext({poolState:null,poolPolling:false,poolRequests:{invalidate(){}},poolRefreshView:{request(){}},coverSubmitting:new Set(),newRecord:value=>value,lastFeedback:null,API_SOURCES:{},$:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id)}});
 vm.runInContext(helpers+source.slice(source.indexOf('function applyPool('),source.indexOf('async function poolRequest(')),context);
 context.applyPool({revision:1,preferences:{explanation:'当前偏好',learning:{uniqueExamples:0,blockedTypes:[]},cover:{enabled:true,count:4,references:names.map((name,index)=>({name,sha256:String(index)}))}},ready:[],readyCount:50,reserveCount:5,usedCount:6,status:'已就绪',lastFeedback:{id:'story-vote'}});
 const summary=elements.get('preferenceSummary');assert.match(summary.textContent,/封面参考：Lindsey/);assert.match(summary.textContent,/优先相近构图／穿着风格（不露点）/);
 for(const name of names)assert.ok(summary.title.includes(name));assert.equal(context.lastFeedback,'story-vote');assert.equal(elements.get('undoFeedback').hidden,false);
});

test('cover match annotation names the reference without turning a cosine into a percentage',()=>{
 const children=[],context=vm.createContext({r:{coverPreference:{referenceName:'Lindsey',score:0.9137}},nameBox:{append:value=>children.push(value)},element:(tag,cls,textContent)=>({tag,cls,textContent})});
 vm.runInContext(source.split('\n').find(line=>line.startsWith(' if(r.coverPreference?.referenceName)')),context);
 assert.equal(children[0].textContent,'封面相近：Lindsey');assert.match(children[0].title,/构图／穿着风格相近/);assert.ok(!JSON.stringify(children).includes('91'));assert.ok(!JSON.stringify(children).includes('%'));
});

test('repeated cover clicks share one in-flight request and clear their busy state after failure',async()=>{
 let reject;const waiting=new Promise((_,fail)=>{reject=fail}),{context,requests,messages}=fixture({request:()=>waiting});
 const first=context.coverFeedbackBot('lindsey','more');await context.coverFeedbackBot('lindsey','more');assert.equal(requests.length,1);assert.equal(context.coverFeedbackState(approved).disabled,true);
 reject(new Error('保存失败'));await first;assert.equal(context.coverSubmitting.size,0);assert.equal(context.lastFeedback,'existing-story-vote');assert.deepEqual(messages,['保存失败']);
});
