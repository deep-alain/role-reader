import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
const source=await fs.readFile(new URL('../extension/app.js',import.meta.url),'utf8');
const build=source.slice(source.indexOf('function recordActions('),source.indexOf('let actionNoticeTimer;'));
function element(tag,cls,textContent){return {tag,cls,textContent,children:[],append(...items){this.children.push(...items)}}}
function fixture(){const calls=[],context=vm.createContext({element,consuming:new Set(),feedbackSubmitting:new Set(),supportsTypeExclusion:()=>true,document:{querySelectorAll:()=>[]},openEditor:id=>calls.push(['view',id]),consumeBot:id=>calls.push(['use',id]),feedbackBot:(...v)=>calls.push(v),coverFeedbackState:()=>({label:'封面多来',choice:'more',disabled:false}),coverFeedbackBot:(...v)=>calls.push(['cover',...v])});vm.runInContext(build,context);return {context,calls}}
test('rows expose three controls with preference actions inside More',()=>{
 const {context,calls}=fixture(),row=context.recordActions({id:'one'},'pool');
 assert.deepEqual(row.children.map(x=>x.textContent),['查看','已使用',undefined]);
 const menu=row.children[2];assert.equal(menu.tag,'details');assert.equal(menu.children[0].textContent,'更多');
 const options=menu.children[1].children;assert.deepEqual(options.map(x=>x.textContent),['多来','不要此类','封面多来']);
 menu.open=true;options[1].onclick();assert.equal(menu.open,false);assert.deepEqual(calls,[['one','less']]);
});
test('pending feedback disables preference actions and shows processing',()=>{
 const {context}=fixture();context.feedbackSubmitting.add('one');const row=context.recordActions({id:'one'},'pool'),options=row.children[2].children[1].children;
 assert.equal(options[0].disabled,true);assert.equal(options[1].disabled,true);assert.equal(options[1].textContent,'处理中…');
});
