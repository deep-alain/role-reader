import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
const source=await fs.readFile(new URL('../extension/app.js',import.meta.url),'utf8');
const helper=source.slice(source.indexOf('function supportsTypeExclusion('),source.indexOf('function applyPool('));

test('excluded type summary includes every label and has a full hover representation',()=>{
 const context=vm.createContext({});vm.runInContext(helper,context);
 const labels=Array.from({length:20},(_,i)=>'室友与同居和人物特征'+i),learning={blockedTypes:labels.map(label=>({label}))};
 const shortened=context.blockedTypesMessage(learning),full=context.blockedTypesMessage(learning,Infinity);
 assert.match(shortened,/已排除类型/);assert.match(shortened,/共 20 类/);assert.match(shortened,/悬停查看全部/);
 for(const label of labels)assert.ok(full.includes(label));
 assert.equal(context.blockedTypesMessage({}), '');assert.equal(context.blockedTypesMessage(null), '');
});

test('feedback displays server effect and excluded types while retaining undo',async()=>{
 const messages=[],requests=[],undo={hidden:true},learning={uniqueExamples:4,blockedTypes:[{key:'combo',label:'室友与同居 + 占有欲'}]};
 const context=vm.createContext({libraryView:'pool',lastFeedback:null,poolState:{preferences:{learning}},poolRequest:async(path,body)=>{requests.push({path,...body});return {effect:body.choice==='clear'?'已撤销此次类型排除':'已排除此类',learning:body.choice==='clear'?{uniqueExamples:3,blockedTypes:[]}:learning,pool:{}}},applyPool(){},refreshBatchResults:async()=>{},$:id=>id==='undoFeedback'?undo:null,message:value=>messages.push(value)});
 vm.runInContext(helper+source.slice(source.indexOf('async function feedbackBot('),source.indexOf("$('undoFeedback').onclick=")),context);
 await context.feedbackBot('role-1','less');
 assert.equal(requests[0].choice,'less');assert.equal(context.lastFeedback,'role-1');assert.equal(undo.hidden,false);assert.match(messages[0],/已排除此类/);assert.match(messages[0],/已排除类型：室友与同居 \+ 占有欲/);
 await context.feedbackBot('role-1','clear');
 assert.equal(context.lastFeedback,null);assert.equal(undo.hidden,true);assert.match(messages[1],/已撤销此次类型排除/);assert.ok(!messages[1].includes('已排除类型'));
});

test('old backend cannot receive a type-exclusion vote until the tool restarts',async()=>{
 const messages=[];let requests=0;
 const context=vm.createContext({poolState:{preferences:{learning:{uniqueExamples:3}}},poolRequest:async()=>{requests++;throw new Error('must not submit')},message:value=>messages.push(value)});
 vm.runInContext(helper+source.slice(source.indexOf('async function feedbackBot('),source.indexOf("$('undoFeedback').onclick=")),context);
 assert.equal(context.supportsTypeExclusion(),false);await context.feedbackBot('role-1','less');
 assert.equal(requests,0);assert.match(messages[0],/服务尚未更新，请重启工具后启用类型排除/);assert.match(messages[0],/没有提交/);
 context.poolState={preferences:{learning:{blockedTypes:[]}}};assert.equal(context.supportsTypeExclusion(),true);
});
