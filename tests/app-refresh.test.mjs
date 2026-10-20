import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {requestGate,visibleRecords} from '../extension/list-view.js';
const source=await fs.readFile(new URL('../extension/app.js',import.meta.url),'utf8');
const refreshSource=source.slice(source.indexOf('async function refreshPool('),source.indexOf('async function consumeBot('));

test('actual refresh handler ignores a late old-view response with equal revision',async()=>{
 const requests=[],applied=[],context=vm.createContext({poolAvailable:true,poolPolling:false,libraryView:'pool',poolState:null,viewRevisions:{},poolRequests:requestGate(),URLSearchParams,AbortSignal,
  fetch:(url)=>new Promise(resolve=>requests.push({url,resolve})),applyPool:next=>applied.push(next),$:()=>({textContent:''})});
 vm.runInContext(refreshSource,context);const old=context.refreshPool(true);
 context.libraryView='used';const fresh=context.refreshPool(true);
 requests[1].resolve({ok:true,json:async()=>({revision:10,history:[{id:'used'}]})});await fresh;
 requests[0].resolve({ok:true,json:async()=>({revision:10,ready:[{id:'ready'}]})});await old;
 assert.equal(applied.length,1);assert.equal(applied[0].history[0].id,'used');assert.equal(context.poolPolling,false);
});
test('a view with no complete cache requests full data even when global revision is unchanged',async()=>{
 const urls=[],context=vm.createContext({poolAvailable:true,poolPolling:false,libraryView:'used',poolState:{revision:10},viewRevisions:{pool:10},poolRequests:requestGate(),URLSearchParams,AbortSignal,
  fetch:async url=>{urls.push(url);return {ok:true,json:async()=>({revision:10,history:[]})}},applyPool(){},$:()=>({textContent:''})});
 vm.runInContext(refreshSource,context);await context.refreshPool();assert.ok(!urls[0].includes('since='));await context.refreshPool();assert.ok(urls[1].includes('since=10'));
});
test('actual export button exports the same filtered and sorted list as the table',()=>{
 let exported;const button={},records=[{name:'A',status:'complete',sourceRating:'NSFW',sourceMetrics:{messages:2}},{name:'B',status:'partial',sourceRating:'SFW'},{name:'A2',status:'complete',sourceRating:'NSFW',sourceMetrics:{messages:9}}];
 const context=vm.createContext({$:()=>button,currentVisible:()=>visibleRecords(records,{query:'A',rating:'NSFW',order:'messages'}),publicRecord:r=>r,exportRecords:value=>{exported=value}});
 vm.runInContext(source.split('\n').find(line=>line.startsWith("$('export').onclick=")),context);button.onclick();assert.deepEqual(exported.map(r=>r.name),['A2','A']);
});
