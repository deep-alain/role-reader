import test from 'node:test';
import assert from 'node:assert/strict';
import {reconcileRows,selectionSafeRefresh} from '../extension/table-refresh.js';

function harness(){
 const document=new EventTarget(),window=new EventTarget(),tasks=[];
 let selected=false,dialog=false,renders=0;
 document.querySelector=()=>dialog?{}:null;
 document.getSelection=()=>({isCollapsed:!selected});
 document.activeElement=null;
 const gate=selectionSafeRefresh({document,window,render:()=>renders++,schedule:fn=>tasks.push(fn)});
 return {document,window,gate,select:value=>selected=value,dialog:value=>dialog=value,
  run(){while(tasks.length)tasks.shift()()},get renders(){return renders}};
}
function fire(target,type,properties={}){const event=new Event(type);Object.assign(event,properties);target.dispatchEvent(event)}

test('polling during a mouse drag is coalesced until the drag ends',()=>{
 const h=harness();fire(h.document,'pointerdown',{button:0});
 h.gate.request();h.gate.request();h.run();assert.equal(h.renders,0);
 fire(h.window,'pointerup');h.run();assert.equal(h.renders,1);
});
test('releasing the mouse keeps the selected text intact until selection clears',()=>{
 const h=harness();fire(h.document,'pointerdown',{button:0});h.select(true);
 h.gate.request();fire(h.window,'pointerup');h.run();assert.equal(h.renders,0);
 h.gate.request();h.run();assert.equal(h.renders,0);
 h.select(false);fire(h.document,'selectionchange');h.run();assert.equal(h.renders,1);
});
test('explicit mutations refresh immediately even with a persistent selection',()=>{
 const h=harness();h.select(true);h.gate.request();h.run();assert.equal(h.renders,0);
 h.gate.request({immediate:true});assert.equal(h.renders,1);
 h.run();assert.equal(h.renders,1);
 h.gate.request();h.run();assert.equal(h.renders,1);
 h.select(false);fire(h.document,'selectionchange');h.run();assert.equal(h.renders,2);
});
test('an open row menu holds background updates until it closes',()=>{
 const h=harness();let open=true;h.document.querySelector=selector=>selector.includes('details.record-more[open]')&&open?{}:null;
 h.gate.request();h.run();assert.equal(h.renders,0);
 open=false;fire(h.document,'toggle');h.run();assert.equal(h.renders,1);
});
test('keyboard selections in textareas also defer background rendering',()=>{
 const h=harness();h.document.activeElement={selectionStart:4,selectionEnd:100};
 h.gate.request();h.run();assert.equal(h.renders,0);
 h.document.activeElement.selectionEnd=4;fire(h.document,'keyup');h.run();assert.equal(h.renders,1);
});
test('an open details dialog defers updates and closing it applies the latest model once',()=>{
 const h=harness();h.dialog(true);h.gate.request();h.run();h.gate.request();h.run();
 assert.equal(h.renders,0);h.dialog(false);fire(h.document,'close');h.run();assert.equal(h.renders,1);
});
test('pointer cancellation and window blur cannot leave refresh permanently locked',()=>{
 for(const type of ['pointercancel','blur']){
  const h=harness();fire(h.document,'pointerdown',{button:0});h.gate.request();h.run();
  fire(h.window,type);h.run();assert.equal(h.renders,1);
 }
});
test('disposing a queued refresh removes listeners and prevents rendering',()=>{
 const h=harness();h.gate.request();h.gate.dispose();h.run();assert.equal(h.renders,0);
});

function table(){
 const children=[];let mutations=0;
 return {children,get mutations(){return mutations},get firstChild(){return children[0]||null},
  insertBefore(node,cursor){mutations++;const old=children.indexOf(node);if(old>=0)children.splice(old,1);const at=cursor?children.indexOf(cursor):children.length;assert.ok(at>=0);children.splice(at,0,node)},
  removeChild(node){mutations++;children.splice(children.indexOf(node),1)},
  create(item){const node={id:item.id,value:item.value};Object.defineProperty(node,'nextSibling',{get:()=>children[children.indexOf(node)+1]||null});return node}
 };
}
function update(t,items){reconcileRows(t,items,{key:r=>r.id,version:r=>r.value,create:r=>t.create(r)})}
test('unchanged rows retain the exact text nodes through repeated polls',()=>{
 const t=table(),rows=[{id:'a',value:'Selected text'},{id:'b',value:'Other row'}];
 update(t,rows);const first=t.children[0],second=t.children[1],mutations=t.mutations;
 for(let i=0;i<10;i++)update(t,rows.map(r=>({...r,backgroundRevision:i})));
 assert.equal(t.children[0],first);assert.equal(t.children[1],second);assert.equal(t.mutations,mutations);
});
test('changed rows update without replacing unaffected rows or their images',()=>{
 const t=table();update(t,[{id:'a',value:1},{id:'b',value:1}]);const oldA=t.children[0],oldB=t.children[1];
 update(t,[{id:'a',value:2},{id:'b',value:1}]);assert.notEqual(t.children[0],oldA);assert.equal(t.children[1],oldB);
 assert.deepEqual(t.children.map(n=>n.value),[2,1]);
});
test('filtering, reordering and replacement leave no stale or duplicate rows',()=>{
 const t=table();update(t,[{id:'a',value:1},{id:'b',value:1},{id:'c',value:1}]);const c=t.children[2];
 update(t,[{id:'c',value:1},{id:'a',value:1}]);assert.equal(t.children[0],c);assert.deepEqual(t.children.map(n=>n.id),['c','a']);
 update(t,[{id:'d',value:2},{id:'a',value:3}]);assert.deepEqual(t.children.map(n=>n.id),['d','a']);
 update(t,[]);assert.equal(t.children.length,0);
});
