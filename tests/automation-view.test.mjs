import test from 'node:test';
import assert from 'node:assert/strict';
import {automationView,automationController} from '../extension/automation-view.js';

test('approved future candidates are distinct from newly filled ready slots',()=>{
 const view=automationView({job:{id:'batch-1',status:'completed',read:120,accepted:17,readyAdded:2,pending:8,reviewed:20,rejected:95}});
 assert.equal(view.active,false);assert.equal(view.accepted,17);assert.equal(view.readyAdded,2);
 assert.match(view.summary,/审核通过 17 条/);assert.match(view.summary,/进入待用／备用 2 条/);assert.match(view.summary,/待核验 8 条/);
 assert.doesNotMatch(view.summary,/%|已补满|50 个/);
});

test('one failed source is visible while other sources keep reading',()=>{
 const view=automationView({job:{status:'running',read:7,perSource:{chub:{status:'failed',read:0,error:'HTTP 401，需要登录授权'},botbooru:{status:'running',read:7,accepted:2}}},sources:['chub','botbooru']});
 assert.equal(view.active,true);assert.equal(view.sources.length,2);
 assert.match(view.sources[0].error,/401/);assert.match(view.sources[0].status,/不可用/);
 assert.equal(view.sources[1].status,'读取中');assert.match(view.sources[1].detail,/实读 7.*通过 2/);
});

test('metadata-only search never appears to have read complete profiles',()=>{
 const view=automationView({sources:[{id:'spicychat',label:'SpicyChat',metadataOnly:true}],job:{perSource:{spicychat:{read:99,status:'login_required'}}}});
 assert.equal(view.sources[0].status,'需要登录授权');assert.match(view.sources[0].detail,/不计入完整角色或入库数/);
 assert.doesNotMatch(view.sources[0].detail,/实读 99/);
});

test('missing image reviewer reports waiting instead of promising autonomous approval',()=>{
 const view=automationView({job:{status:'waitingReview',pending:10},reviewer:{available:false,note:'请配置本机视觉模型；已有 10 条等待核验。'}});
 assert.match(view.title,/等待头像核验/);assert.match(view.reviewerNote,/配置本机视觉模型/);
 assert.doesNotMatch(view.reviewerNote,/已连接/);
});

test('source failure reasons and configured model are displayed without inventing counters',()=>{
 const view=automationView({job:{reasonCounts:{'重复角色':4,'热度不足':2,'未成年':0},perSource:{custom:{read:-5,accepted:'3'}}},reviewer:{available:true,backend:'local-vision'},sources:{custom:{label:'自定义渠道'}}});
 assert.match(view.reviewerNote,/local-vision/);assert.equal(view.reasons,'重复角色 4；热度不足 2');
 assert.equal(view.sources[0].label,'自定义渠道');assert.match(view.sources[0].detail,/实读 0.*通过 3/);
});

function harness({get=async()=>({job:{status:'idle'}}),post=async()=>({job:{status:'running'}})}={}){
 const draws=[],tasks=new Map();let serial=0;
 const controller=automationController({get,post,render:(view,meta)=>draws.push({view,meta}),schedule:(fn,delay)=>{const id=++serial;tasks.set(id,{fn,delay});return id},unschedule:id=>tasks.delete(id)});
 return {controller,draws,tasks};
}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}

test('reloading restores running progress and polls independently every two seconds',async()=>{
 let calls=0;const h=harness({get:async()=>{calls++;return {job:{id:'persisted',status:'running',read:37}}}});
 await h.controller.refresh();assert.equal(calls,1);assert.equal(h.draws.at(-1).view.read,37);
 assert.equal([...h.tasks.values()][0].delay,2000);h.controller.dispose();assert.equal(h.tasks.size,0);
});

test('a double start click sends one backend job and exposes submitting state',async()=>{
 const next=deferred(),posts=[];const h=harness({post:(path,body)=>{posts.push({path,body});return next.promise}});
 const first=h.controller.start();assert.equal(h.draws.at(-1).meta.busy,true);
 assert.equal(await h.controller.start(),false);assert.equal(posts.length,1);
 next.resolve({job:{status:'running',read:0}});assert.equal(await first,true);
 assert.equal(h.draws.at(-1).view.active,true);assert.equal(h.draws.at(-1).meta.busy,false);
 assert.deepEqual(posts[0],{path:'/api/automation/start',body:{maxRead:120}});
 assert.equal(await h.controller.start(),false);h.controller.dispose();
});

test('a stale progress request cannot replace the newly started job',async()=>{
 const stale=deferred();const h=harness({get:()=>stale.promise,post:async()=>({job:{id:'new',status:'running',read:2}})});
 const refresh=h.controller.refresh();await h.controller.start();stale.resolve({job:{status:'completed',read:900}});await refresh;
 assert.equal(h.draws.at(-1).view.active,true);assert.equal(h.draws.at(-1).view.read,2);h.controller.dispose();
});

test('cancellation keeps progress counters and switches to idle polling',async()=>{
 const posts=[];const h=harness({get:async()=>({job:{status:'running',read:28,accepted:3}}),post:async(path,body)=>{posts.push({path,body});return {job:{status:'cancelled',read:28,accepted:3}}}});
 await h.controller.refresh();await h.controller.cancel();
 assert.equal(h.draws.at(-1).view.read,28);assert.equal(h.draws.at(-1).view.accepted,3);assert.equal(h.draws.at(-1).view.active,false);
 assert.equal([...h.tasks.values()][0].delay,15000);assert.equal(posts[0].path,'/api/automation/cancel');h.controller.dispose();
});

test('start failures remain visible after the busy state clears',async()=>{
 const h=harness({post:async()=>{throw new Error('接口返回 503')}});assert.equal(await h.controller.start(),false);
 assert.equal(h.draws.at(-1).meta.busy,false);assert.match(h.draws.at(-1).meta.error,/503/);h.controller.dispose();
});
