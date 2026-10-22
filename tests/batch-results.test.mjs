import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
const source=await fs.readFile(new URL('../extension/app.js',import.meta.url),'utf8');

test('one click starts automation and opens the actual batch results without URLs',async()=>{
 const views=[],messages=[];let starts=0;
 const context=vm.createContext({automation:{start:async()=>{starts++;return true}},localApi:true,poolAvailable:true,running:false,setLibraryView:async view=>views.push(view),message:value=>messages.push(value)});
 vm.runInContext(source.slice(source.indexOf('async function startAutomation('),source.indexOf("$('automationStart').onclick=")),context);
 await context.startAutomation();assert.equal(starts,1);assert.deepEqual(views,['batch']);assert.ok(messages[0].includes('通过的角色'));
});
test('actual batch result loading never mutates ready stock or usage history',async()=>{
 let renders=0;const pool={ready:[{id:'old'}],history:[{id:'used'}]},context=vm.createContext({batchRequest:0,batchRecords:[],batchStamp:'x',libraryView:'batch',poolState:pool,newRecord:r=>r,AbortSignal,poolRefreshView:{request(){renders++}},message(){},fetch:async()=>({ok:true,json:async()=>({jobId:'job',records:[{id:'new'}]})})});
 vm.runInContext(source.slice(source.indexOf('async function refreshBatchResults('),source.indexOf('function showAutomation(')),context);
 await context.refreshBatchResults();assert.equal(context.batchRecords[0].id,'new');assert.equal(pool.ready[0].id,'old');assert.equal(pool.history[0].id,'used');assert.equal(renders,1);
});
