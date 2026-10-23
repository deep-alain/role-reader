import {FIELDS,text,extractUrls,safeUrl,newRecord,cardToRecord,pageToRecord,dedupeKey,publicRecord} from './core.js';
import {readZip,readPngCard,makeXlsx,makeZip} from './zip.js';
import {API_SOURCES,detectApiSource,apiPermissionUrls,searchApi,readApiCharacter} from './apis.js';
import {visibleRecords,requestGate} from './list-view.js';
import {reconcileRows,selectionSafeRefresh} from './table-refresh.js';
import {automationController} from './automation-view.js';
const $=id=>document.getElementById(id),extended=!!globalThis.chrome?.scripting,storageKey='role-reader-v1';
const sessionPort=extended?chrome.runtime.connect({name:'role-reader-session'}):null;
if(sessionPort)setInterval(()=>{try{sessionPort.postMessage({type:'keepalive'})}catch{}},20000);
let records=[],running=false,stopped=false,editId=null,localApi=false,jobController=null,sourceTab=Number(new URLSearchParams(location.search).get('sourceTab'))||null;
let libraryView='manual',poolAvailable=false,poolState=null,poolPolling=false,editLibrary='manual';
const consuming=new Set(),feedbackSubmitting=new Set(),coverSubmitting=new Set(),poolRequests=requestGate(),viewRevisions={};
let lastFeedback=null;
let automation=null,automationActive=false,automationBusy=false,batchRecords=[],batchStamp='',batchRequest=0;
const poolRefreshView=selectionSafeRefresh({document,window,render});
const displayedRecords=()=>libraryView==='pool'?(poolState?.ready||[]):libraryView==='used'?(poolState?.history||[]):libraryView==='pending'?(poolState?.portraitPending||[]):libraryView==='batch'?batchRecords:records;
const currentVisible=()=>visibleRecords(displayedRecords(),{query:$('search').value,filter:$('filter').value,rating:$('sourceRatingFilter').value,order:$('recordOrder').value});
const apiFetch=(url,options)=>fetch(localApi?'/api/fetch?url='+encodeURIComponent(url):url,options);
async function openRules(){
 $('rankingRules').showModal();$('ruleStatus').textContent='正在读取已保存的规则…';$('saveRules').disabled=true;
 try{const response=await fetch('/api/rules',{cache:'no-store',signal:AbortSignal.timeout(10000)}),value=await response.json();if(!response.ok)throw new Error(value.error||'规则读取失败');for(const key of ['minimumViews','minimumMessages','minimumLikes'])$(key).value=value.rules[key];$('ruleExplanation').replaceChildren(...value.explanation.map(line=>element('li','',line)));$('ruleStatus').textContent='0 表示不设门槛；设为正数后，缺少该计数的记录也不显示。库存仍保留。';$('saveRules').disabled=false}catch(error){$('ruleStatus').textContent=error.message}
}
$('rulesOpen').onclick=openRules;
$('saveRules').onclick=async()=>{const body={};for(const key of ['minimumViews','minimumMessages','minimumLikes']){const raw=$(key).value.trim(),value=Number(raw);if(!raw||!Number.isSafeInteger(value)||value<0||value>1e12){$('ruleStatus').textContent='请输入 0 至 1 万亿的整数';return}body[key]=value}$('saveRules').disabled=true;try{const result=await poolRequest('/api/rules',body);$('recordOrder').value='rules';applyPool(result.pool);$('rankingRules').close();message('排序规则已保存；当前显示 '+result.pool.matchingCount+' / '+result.pool.readyCount+' 个待用角色，库存和使用次数未改变。')}catch(error){$('ruleStatus').textContent=error.message}finally{$('saveRules').disabled=false}};
let discoveryPage=1,discoveryNext=null;
async function discoverCandidates(page=1){if(!localApi)return;for(const id of ['discoverSource','discoverQuery','discoverSort','discoverNsfw'])$(id).disabled=true;$('discoverSearch').disabled=true;$('discoverNext').disabled=true;$('discoverNote').textContent='正在读取原站公开 NSFW 热榜…';try{const params=new URLSearchParams({source:$('discoverSource').value,q:$('discoverQuery').value,sort:$('discoverSort').value,nsfw:$('discoverNsfw').checked?'1':'0',page});const response=await fetch('/api/discovery?'+params,{cache:'no-store',signal:AbortSignal.timeout(60000)}),value=await response.json();if(!response.ok)throw new Error(value.error||'候选读取失败');discoveryPage=page;discoveryNext=value.nextPage;$('discoverNote').textContent=value.note+' 本页 '+value.items.length+' 个候选'+(typeof value.found==='number'?'；来源匹配 '+value.found.toLocaleString('zh-CN')+' 个':'')+'。成年设定还需详情确认，候选不计入待用 50 个。';$('discoverRows').replaceChildren();for(const r of value.items){const row=element('tr'),name=element('td'),link=element('a','',r.name||r.id);link.href=r.url;link.target='_blank';link.rel='noopener noreferrer';name.append(link);row.append(name,element('td','',r.sourceRating));const labels={messages:'消息',likes:'点赞',liked:'喜欢',clicks:'点击',rating:'评分'},stat=Object.entries(r.metrics).filter(([,v])=>typeof v==='number').map(([k,v])=>labels[k]+' '+v.toLocaleString('zh-CN')).join(' · ');row.append(element('td','',stat||'原站未提供'),element('td','',r.definitionAccess));$('discoverRows').append(row)}}catch(e){$('discoverNote').textContent='读取失败：'+e.message;$('discoverRows').replaceChildren();discoveryNext=null}finally{for(const id of ['discoverSource','discoverQuery','discoverSort','discoverNsfw'])$(id).disabled=false;$('discoverSearch').disabled=false;$('discoverNext').disabled=!discoveryNext}}
$('discoverOpen').onclick=()=>{$('discovery').showModal();if(!$('discoverRows').children.length)discoverCandidates(1)};
$('discoverSearch').onclick=()=>discoverCandidates(1);$('discoverNext').onclick=()=>{if(discoveryNext)discoverCandidates(discoveryNext)};
$('discoverSource').onchange=()=>{const spicy=$('discoverSource').value==='spicychat';$('discoverSort').options[1].textContent=spicy?'评分优先（无点赞字段）':'本批点赞优先';if(!spicy&&$('discoverQuery').value==='roommate')$('discoverQuery').value='gothic';else if(spicy&&$('discoverQuery').value==='gothic')$('discoverQuery').value='roommate';$('discoverRows').replaceChildren();$('discoverNote').textContent='已切换来源，点击“读取热门候选”查看结果。';discoveryNext=null;$('discoverNext').disabled=true};
function avatarUrl(r){if(localApi&&r.addedAt&&r.avatarVisibility==='source')return '/api/pool/avatar?id='+encodeURIComponent(r.id)+'&v='+encodeURIComponent(r.avatarAsset?.sha256||'');return /^data:image\/(?:png|jpeg|webp|gif|svg\+xml);base64,/i.test(r.avatar)?r.avatar:safeUrl(r.avatar)}
const message=s=>{$('status').textContent=s};
function element(tag,cls,content){const el=document.createElement(tag);if(cls)el.className=cls;if(content!=null)el.textContent=content;return el}
async function refreshBatchResults(){
 const token=++batchRequest;
 try{const response=await fetch('/api/automation/results',{cache:'no-store',signal:AbortSignal.timeout(10000)}),result=await response.json();if(token!==batchRequest)return;if(!response.ok)throw new Error(result.error||'本轮结果读取失败');batchRecords=(result.records||[]).map(newRecord);if(libraryView==='batch')poolRefreshView.request()}catch(error){batchStamp='';message('本轮结果暂时不可用：'+error.message)}
}
function showAutomation(view,{busy,error}){
 automationActive=view.active;automationBusy=busy;
 $('viewBatch').textContent='本轮通过 '+view.accepted;
 const stamp=(view.jobId||'')+':'+view.accepted;if(libraryView==='batch'&&stamp!==batchStamp){batchStamp=stamp;refreshBatchResults()}
 $('automationPanel').setAttribute('aria-busy',String(view.active||busy));
 $('automationStart').disabled=busy||view.active||running;$('automationStart').textContent=view.active?'正在批量读取…':busy?'正在启动…':'开始批量读取';
 $('automationCancel').hidden=!view.active;$('automationCancel').disabled=busy;
 $('automationTitle').textContent=view.title;$('automationSummary').textContent=view.summary;$('automationStage').textContent=view.stage;$('automationReviewer').textContent=view.reviewerNote;
 $('automationError').textContent=error;$('automationError').hidden=!error;
 $('automationReasons').hidden=!view.reasons;$('automationReasonText').textContent=view.reasons;
 reconcileRows($('automationSources'),view.sources,{key:source=>source.id,version:source=>JSON.stringify(source),create:source=>{
  const row=element('li'),heading=element('div','automation-source-heading');heading.append(element('strong','',source.label),element('span','pill',source.status));row.append(heading,element('p','',source.detail));if(source.error)row.append(element('small','',source.error));return row;
 }});
}
async function initAutomation(){
 if(!localApi||!poolAvailable)return;
 $('automationPanel').hidden=false;
 automation=automationController({get:async()=>{const response=await fetch('/api/automation',{cache:'no-store',signal:AbortSignal.timeout(10000)}),value=await response.json();if(!response.ok)throw new Error(value.error||'读取流程暂时不可用');return value},post:poolRequest,render:showAutomation});
 await automation.refresh();
}
async function startAutomation(){
 if(!automation||!localApi||!poolAvailable)return message('自动批量读取需要连接本机接口工具。');
 if(running)return message('请等当前手动读取或导出完成，再开始自动批量读取。');
 if(await automation.start()){await setLibraryView('batch');message('自动读取已启动：正在跨来源搜索、读取完整资料、去重和核验；通过的角色会出现在下方。')}
}
$('automationStart').onclick=startAutomation;
$('automationCancel').onclick=async()=>{if(automation&&await automation.cancel())message('已请求停止本轮读取，已读取和已通过的资料会保留。')};
async function persist(){try{if(extended)await chrome.storage.local.set({[storageKey]:records});else localStorage.setItem(storageKey,JSON.stringify(records))}catch(e){message('记录未能保存到浏览器：'+e.message+'。请及时导出结果。')}}
function upsert(record){const key=dedupeKey(record),index=records.findIndex(r=>dedupeKey(r)===key);if(index<0)records.push(record);else{const old=records[index];if(old.status!=='failed'&&record.status==='failed'){old.notes=(old.notes||'')+'\n再次读取失败：'+record.error;return}const merged={...old,...record,id:old.id,fieldSources:{...old.fieldSources,...record.fieldSources}};for(const[k]of FIELDS)if(!record[k]||old.fieldSources[k]==='手动编辑'){merged[k]=old[k];if(old.fieldSources[k])merged.fieldSources[k]=old.fieldSources[k]}merged.raw={previous:old.raw,current:record.raw};records[index]=newRecord(merged)}}
function render(){
 const listed=displayedRecords();
 $('total').textContent=listed.length;$('sideCount').textContent=poolAvailable?poolState?.readyCount||0:records.length;
 for(const status of ['complete','partial','failed'])$(status).textContent=listed.filter(r=>r.status===status).length;
 $('export').disabled=!listed.length||running;$('clear').disabled=running;$('clear').hidden=libraryView!=='manual';$('start').disabled=running;$('readCurrent').disabled=running||!extended;$('files').disabled=running;$('apiSearch').disabled=running;$('stop').hidden=!running;
 $('automationStart').disabled=running||automationActive||automationBusy;
 $('recordsTitle').textContent=libraryView==='pool'?'待用角色库':libraryView==='used'?'已使用记录':libraryView==='pending'?'补货候选（含头像待核验，不计入待用）':libraryView==='batch'?'本轮自动通过（满库时等待补位）':'手动读取结果';
 document.querySelector('.input-card').hidden=poolAvailable&&libraryView!=='manual';
 for(const [id,view]of [['viewPool','pool'],['viewUsed','used'],['viewPending','pending'],['viewBatch','batch'],['viewManual','manual']])$(id).classList.toggle('selected',libraryView===view);
 const visible=currentVisible();
 $('export').disabled=!visible.length||running;$('export').textContent='导出当前列表（'+visible.length+'）';
 $('empty').hidden=visible.length>0;$('empty').querySelector('strong').textContent=listed.length?'没有符合筛选条件的角色':libraryView==='pool'?(poolState?.readyCount?'没有符合热度门槛的角色，库存仍保留':'正在自动读取新 bot，请稍候'):'角色资料会显示在这里';
 reconcileRows($('rows'),visible,{key:r=>r.id,version:r=>JSON.stringify([libraryView,supportsTypeExclusion(),coverFeedbackState(r),consuming.has(r.id),feedbackSubmitting.has(r.id),avatarUrl(r),...['name','category','gender','age','status','missing','error','toneMatches','usedAt','source','method','avatarVisibility','avatarError','pendingReason','sourceMetrics','sourceRating','metricsAt','ranking','preferenceReason','userPreference','coverPreference'].map(key=>r[key])]),create:r=>{const tr=element('tr'),nameCell=element('td'),person=element('div','person'),icon=element('span','avatar-placeholder',r.name?.slice(0,1)||'?'),nameBox=element('div');
 const avatar=avatarUrl(r);if(avatar){const img=element('img','record-avatar');img.src=avatar;img.alt=r.avatarVisibility==='covered'?'头像待检查':r.name+'的来源头像';img.loading='lazy';img.referrerPolicy='no-referrer';img.title='点击查看角色和大图';img.onclick=()=>openEditor(r.id);img.onerror=()=>{const failed=element('span','avatar-placeholder','图片加载失败');failed.title=r.avatarError||'来源头像暂时不可用';img.replaceWith(failed)};person.append(img)}else person.append(icon);
 nameBox.append(element('strong','',r.name||'未读取到名称'),element('small','',['used','batch'].includes(libraryView)&&r.usedAt?'已使用 · '+new Date(r.usedAt).toLocaleString('zh-CN'):r.toneMatches?.join(' · ')||r.id.slice(0,8)));person.append(nameBox);nameCell.append(person);tr.append(nameCell);
 const metrics=r.sourceMetrics||{},labels={views:'浏览',favorites:'收藏',downloads:'下载',chats:'聊天',messages:'消息',rating:'评分'};const metricText=Object.entries(labels).filter(([key])=>typeof metrics[key]==='number').map(([key,label])=>label+' '+metrics[key].toLocaleString('zh-CN')).join(' · ');const heat=element('small','source-metrics',metricText||'热度数据未提供');heat.title=(r.metricsAt?'来源数据时间：'+new Date(r.metricsAt).toLocaleString('zh-CN')+'。':'')+'未提供的指标保持未知，收藏不等同点赞。';nameBox.append(heat,element('small','source-rating','原站 '+(r.sourceRating&&r.sourceRating!=='unknown'?r.sourceRating:'分级未知')));if(r.ranking){const basis={views:'浏览',messages:'消息',chats:'聊天',downloads:'下载',favorites:'收藏',likes:'点赞',unknown:'未提供计数'};nameBox.append(element('small','ranking-basis',(r.ranking.ageStatus==='verified'?'成年已确认':'年龄待核验')+' · '+basis[r.ranking.heatBasis]+(r.ranking.heatValue!=null?'排序 · 来源本批第 '+r.ranking.sourcePosition+' / '+r.ranking.sourceBatchSize:'，不当作热门')))}
 if(libraryView==='pending'){const state=element('small','',portraitStatus(r));state.title=r.pendingReason||r.avatarError||'';nameBox.append(state)}
 if(r.preferenceReason)nameBox.append(element('small','taste-badge',concisePreferenceReason(r.preferenceReason)));
 if(r.coverPreference?.referenceName){const match=element('small','taste-badge','封面相近：'+r.coverPreference.referenceName);match.title='封面构图／穿着风格相近，参考：'+r.coverPreference.referenceName+'。仅排序偏好，不改变原站分级。';nameBox.append(match)}
 for(const key of ['category','gender','age']){const cell=element('td',key==='category'?'category-cell':'',text(r[key])||'—');cell.title=text(r[key]);tr.append(cell)}
 const status=element('td'),pill=element('span','pill '+(r.status==='complete'?'good':r.status==='failed'?'bad':''),r.status==='complete'?'9 / 9 字段':r.status==='failed'?'读取失败':`${9-r.missing.length} / 9 字段`);pill.title=r.error||'缺少：'+r.missing.join('、');status.append(pill);tr.append(status);
 const src=element('td','source');if(safeUrl(r.source)){const a=element('a','',new URL(r.source).hostname);a.href=r.source;a.target='_blank';a.rel='noopener noreferrer';a.title=r.source;src.append(a)}else src.textContent=r.source||r.method;tr.append(src);
 const controls=element('td'),actions=recordActions(r,libraryView);controls.append(actions);tr.append(controls);return tr;
 }});
}
function recordActions(record,view){
 const actions=element('div','record-actions'),show=element('button','quiet','查看');show.onclick=()=>openEditor(record.id);actions.append(show);
 if(view==='pool'){const use=element('button','use-button',consuming.has(record.id)?'处理中…':'已使用');use.title='标记已使用并自动补新';use.disabled=consuming.has(record.id);use.onclick=()=>consumeBot(record.id);actions.append(use)}
 if(['pool','batch'].includes(view)){
  const menu=element('details','record-more'),summary=element('summary','','更多'),items=element('div','record-menu');menu.append(summary,items);
  menu.ontoggle=()=>{if(menu.open)for(const other of document.querySelectorAll('details.record-more[open]'))if(other!==menu)other.open=false};
  const busy=feedbackSubmitting.has(record.id);
  for(const[choice,label]of [['more',record.userPreference==='more'?'撤销多来':'多来'],['less',busy?'处理中…':'不要此类']]){
   const button=element('button','quiet',label);button.disabled=busy||(choice==='less'&&!supportsTypeExclusion());button.title=choice==='less'?'排除此类，可撤销':'';
   button.onclick=()=>{menu.open=false;feedbackBot(record.id,choice==='more'&&record.userPreference==='more'?'clear':choice)};items.append(button);
  }
  const state=coverFeedbackState(record),cover=element('button','quiet',state.label);cover.disabled=state.disabled;cover.title=state.title;cover.onclick=()=>{menu.open=false;coverFeedbackBot(record.id,state.choice)};items.append(cover);actions.append(menu);
 }
 return actions;
}
let actionNoticeTimer;
function actionNotice(text){message(text);const toast=$('actionToast');if(!toast)return;toast.textContent=text;toast.hidden=false;clearTimeout(actionNoticeTimer);actionNoticeTimer=setTimeout(()=>{toast.hidden=true},4500)}
function portraitStatus(record){
 if(record.avatarError)return '头像读取失败，可重新读取';
 if(!record.avatarAsset)return '头像尚未缓存';
 if(record.avatarAsset.review==='approved')return '头像已核验';
 return '头像已读取 · '+(record.pendingReason||'等待人像检查');
}
function loadReviewImage(image,url,caption,review){
 review.disabled=true;caption.textContent='正在加载来源原图…';
 image.onload=()=>{review.disabled=false;caption.textContent='原图已加载，请确认人像符合要求后保存检查结果。'};
 image.onerror=()=>{review.disabled=true;caption.textContent='原图加载失败，请重新读取头像后再检查。'};
 image.src=url;
}
function concisePreferenceReason(value){return String(value||'').replace(/[（(]\s*多来\s*\d+\s*[/／]\s*不要\s*\d+\s*[）)]/g,'').replace(/\s+/g,' ').trim()}
function supportsCoverFeedback(){return poolState?.preferences?.cover?.enabled===true}
function coverFeedbackState(record){
 const digest=record.avatarAsset?.sha256,reference=!!record.coverPreference?.reference||!!(digest&&(poolState?.preferences?.cover?.references||[]).some(value=>value.sha256===digest)),enabled=supportsCoverFeedback(),approved=record.avatarAsset?.review==='approved',busy=coverSubmitting.has(record.id);
 return {choice:reference?'clear':'more',label:busy?'正在保存封面偏好…':reference?'撤销封面参考':'封面多来',disabled:!enabled||!approved||busy,title:!enabled?'服务尚未更新，封面偏好尚未生效；请重启工具后使用。':!approved?'请先检查并通过来源头像，再添加封面参考。':reference?'撤销此封面参考，不改变题材偏好和使用记录。':'以这张封面为参考，优先相近构图／穿着风格（不露点）；不改变题材偏好。'};
}
function supportsTypeExclusion(){return Array.isArray(poolState?.preferences?.learning?.blockedTypes)}
function applyPool(next,fromPoll=false){
 if(!fromPoll){poolRequests.invalidate();poolPolling=false;}
 if(poolState&&next.revision<poolState.revision)return;
 const changed=!poolState||next.revision!==poolState.revision||!!next.ready||!!next.history;
 poolState={...poolState,...next};if(next.preferences&&!Object.hasOwn(next,'lastFeedback'))delete poolState.lastFeedback;if(next.ready)poolState.ready=next.ready.map(newRecord);if(next.history)poolState.history=next.history.map(newRecord);if(next.portraitPending)poolState.portraitPending=next.portraitPending.map(newRecord);$('viewPending').textContent='补货候选 '+(poolState.portraitPendingCount||0);
 $('poolReady').textContent=poolState.readyCount;$('poolReserve').textContent=poolState.reserveCount;$('poolUsed').textContent=poolState.usedCount;
 lastFeedback=poolState.lastFeedback?.id||null;$('undoFeedback').hidden=!lastFeedback;
 $('poolStatus').textContent=poolState.status+(poolState.readyCount<50?'；剩余 '+(50-poolState.readyCount)+' 个正在补货':'')+(typeof poolState.matchingCount==='number'&&poolState.matchingCount<poolState.readyCount?'；门槛筛选显示 '+poolState.matchingCount+' / '+poolState.readyCount+' 个，库存保留':'');
 $('poolErrors').textContent=Object.entries(poolState.sourceErrors||{}).map(([source,detail])=>(API_SOURCES[source]?.label||source)+' 暂时不可用：'+detail.error).join('；');
 if(changed)poolRefreshView.request({immediate:!fromPoll});
}
async function poolRequest(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},credentials:'omit',body:JSON.stringify(body),signal:AbortSignal.timeout(30000)});const value=await response.json();if(!response.ok)throw new Error(value.error||'库存操作失败');return value;}
async function refreshPool(force=false){
 if(!poolAvailable||poolPolling&&!force)return;
 const view=libraryView,token=poolRequests.begin(view);poolPolling=true;
 try{
  const params=new URLSearchParams();if(view==='used')params.set('history','1');if(view==='pending')params.set('pending','1');
  if(!force&&poolState&&viewRevisions[view]===poolState.revision)params.set('since',poolState.revision);
  const response=await fetch('/api/pool?'+params,{cache:'no-store',signal:AbortSignal.timeout(10000)}),next=await response.json();
  if(!poolRequests.accept(token,libraryView))return;
  if(!response.ok)throw new Error(next.error||'库存服务暂时不可用');
  applyPool(next,true);viewRevisions[view]=next.revision;
 }catch(e){if(poolRequests.accept(token,libraryView))$('poolStatus').textContent='库存服务连接中断，已显示上次记录。请保持启动窗口运行；恢复连接后会继续补货。'}
 finally{if(poolRequests.accept(token,libraryView))poolPolling=false}
}
async function consumeBot(id){if(consuming.has(id))return;consuming.add(id);render();try{const result=await poolRequest('/api/pool/use',{id});applyPool(result.pool);message(result.alreadyUsed?'此 bot 已归档，没有重复消耗库存。':result.replacementId?'已归档使用记录，新 bot 已补位。':'已归档，正在从接口读取新 bot 补位。')}catch(e){message('标记失败：'+e.message)}finally{consuming.delete(id);render()}}
async function setLibraryView(view){poolRequests.invalidate();poolPolling=false;libraryView=view;$('filter').value='all';$('search').value='';render();if(view==='batch')return refreshBatchResults();if(view!=='manual')await refreshPool(true)}
async function feedbackBot(id,choice){
 if(feedbackSubmitting.has(id))return;
 if(choice==='less'&&!supportsTypeExclusion())return actionNotice('请重启工具后启用类型排除，此次未提交');
 feedbackSubmitting.add(id);poolRefreshView.request({immediate:true});actionNotice('正在保存…');
 try{
  const result=await poolRequest('/api/pool/feedback',{id,choice});applyPool(result.pool);
  if(libraryView==='batch')await refreshBatchResults();
  // Ignore an older response when another completed vote has a newer revision.
  if(result.pool.revision==null||result.pool.revision>=poolState.revision){lastFeedback=result.pool.lastFeedback?.id||(choice==='clear'?null:id);$('undoFeedback').hidden=!lastFeedback;}
  actionNotice(choice==='clear'?'已撤销偏好':choice==='less'?'已排除此类':'已保存偏好');
 }catch(error){actionNotice('保存失败：'+error.message)}finally{feedbackSubmitting.delete(id);poolRefreshView.request({immediate:true})}
}
async function coverFeedbackBot(id,choice){
 if(!supportsCoverFeedback())return message('服务尚未更新，封面偏好尚未生效；请重启工具后使用。此次没有提交。');
 const record=displayedRecords().find(value=>value.id===id);if(!record||record.avatarAsset?.review!=='approved')return message('请先检查并通过来源头像，再添加封面参考。此次没有提交。');
 if(!['more','clear'].includes(choice)||coverSubmitting.has(id))return;
 coverSubmitting.add(id);poolRefreshView.request();
 try{const result=await poolRequest('/api/pool/cover-feedback',{id,choice});applyPool(result.pool);if(libraryView==='batch')await refreshBatchResults();message(result.effect||'封面偏好已保存；题材偏好和使用次数不变。')}catch(error){message(error.message)}finally{coverSubmitting.delete(id);poolRefreshView.request()}
}
$('undoFeedback').onclick=()=>{if(lastFeedback)feedbackBot(lastFeedback,'clear')};
$('viewBatch').onclick=()=>setLibraryView('batch');$('viewPending').onclick=()=>setLibraryView('pending');$('viewPool').onclick=()=>setLibraryView('pool');$('viewUsed').onclick=()=>setLibraryView('used');$('viewManual').onclick=()=>setLibraryView('manual');
$('poolRefresh').onclick=async()=>{try{const result=await poolRequest('/api/pool/refresh',{});applyPool(result.pool);message('已开始检查新 bot；现有未使用库存会保留。')}catch(e){message('检查更新失败：'+e.message)}};
function openEditor(id){const r=displayedRecords().find(x=>x.id===id);if(!r)return;editId=id;editLibrary=libraryView;$('saveRecord').hidden=['pending','batch'].includes(libraryView);$('exportOne').hidden=libraryView==='pending';$('provenance').textContent=r.error||`来源：${r.method}。${r.missing.length?'缺少 '+r.missing.join('、')+'。':'九项字段已填写。'} ${r.notes||''}`;$('editFields').replaceChildren();
 const previewUrl=avatarUrl(r);if(previewUrl){const preview=element('div','avatar-preview'),image=element('img');image.src=previewUrl;image.alt=r.name+'的头像';image.referrerPolicy='no-referrer';preview.append(image);const tools=element('div'),caption=element('small','',portraitStatus(r));tools.append(caption);preview.append(tools);
 if(localApi&&r.avatarAsset&&libraryView==='pending'){const inspect=element('button','quiet','检查来源头像');inspect.type='button';const select=element('select');select.hidden=true;for(const[value,label]of [['glamour_portrait','成熟魅力人像（不露点）'],['detailed_illustration','精细人物插画'],['photo','真人 / 写实人像'],['rendered_portrait','写实人物渲染'],['default_icon','排除：默认图标'],['no_person','排除：没有人像'],['faint_sketch','排除：过淡线稿'],['chibi_cartoon','排除：Q版卡通'],['other_rejected','排除：其他不合适']]){const option=element('option','',label);option.value=value;select.append(option)}const review=element('button','','保存检查结果');review.type='button';review.hidden=true;inspect.onclick=()=>{inspect.hidden=true;select.hidden=review.hidden=false;loadReviewImage(image,'/api/pool/avatar?id='+encodeURIComponent(r.id)+'&preview=1&v='+r.avatarAsset.sha256,caption,review)};review.onclick=async()=>{if(review.disabled)return;review.disabled=true;try{const result=await poolRequest('/api/pool/portrait-review',{id:r.id,sha256:r.avatarAsset.sha256,style:select.value});applyPool(result.pool);$('editor').close();message('检查结果已保存；仅通过的人像进入待用 / 备用库。')}catch(error){message(error.message);review.disabled=false}};tools.append(inspect,select,review)}
 else if(localApi&&r.avatarAsset&&libraryView!=='batch'){const inspect=element('button','quiet','查看来源头像');inspect.type='button';inspect.onclick=()=>{image.src='/api/pool/avatar?id='+encodeURIComponent(r.id)+'&preview=1&v='+r.avatarAsset.sha256;caption.textContent='检查来源图是否符合你的展示要求';inspect.hidden=true;accept.hidden=false;reject.hidden=false};const accept=element('button','','可以展示'),reject=element('button','quiet','隐藏头像');accept.type=reject.type='button';accept.hidden=reject.hidden=true;for(const[button,approved]of [[accept,true],[reject,false]])button.onclick=async()=>{try{await poolRequest('/api/pool/avatar-review',{id:r.id,sha256:r.avatarAsset.sha256,approved});await refreshPool(true);$('editor').close();openEditor(r.id);message(approved?'已切换为来源头像。':'已隐藏该头像。')}catch(e){message(e.message)}};tools.append(inspect,accept,reject)}if(localApi&&libraryView!=='batch'){const retry=element('button','quiet','重新读取头像');retry.type='button';retry.onclick=async()=>{retry.disabled=true;try{const result=await poolRequest('/api/pool/avatar-retry',{id:r.id});applyPool(result.pool);$('editor').close();openEditor(r.id);message('头像已重新读取，变更后的图片需重新审核。')}catch(error){caption.textContent=error.message;retry.disabled=false}};tools.append(retry)}$('editFields').append(preview)}
 for(const[key,label]of FIELDS){const wrap=element('label','edit-field '+(['scenario','greet','intro','prompt'].includes(key)?'wide':'')),title=element('span','',label);let input;
 if(key==='gender'){input=element('select');for(const value of ['','Non-binary','Male','Female']){const option=element('option','',value||'未公开 / 未识别');option.value=value;input.append(option)}}else input=element(['scenario','greet','intro','prompt'].includes(key)?'textarea':'input');
 input.id='edit-'+key;input.value=r[key]||'';if(libraryView==='batch'){input.readOnly=true;if(key==='gender')input.disabled=true;}
 if(key==='avatar'&&libraryView!=='manual'){input.readOnly=true;input.title='来源头像随完整资料更新；新图片需重新核验'}
 if(['scenario','greet','intro','prompt'].includes(key)){const heading=element('span','field-heading'),copy=element('button','quiet copy-field','复制');copy.type='button';copy.setAttribute('aria-label','复制'+label);copy.onclick=async event=>{event.preventDefault();try{await navigator.clipboard.writeText(input.value);copy.textContent='已复制';setTimeout(()=>{copy.textContent='复制'},1500)}catch{input.focus();input.select();message('已选中完整内容，请按 Ctrl+C 复制。')}};heading.append(title,copy);wrap.append(heading)}else wrap.append(title);
 wrap.append(input,element('small','',r.fieldSources[key]||(libraryView==='batch'?'原站未公开此字段':'缺失，可手动补充')));$('editFields').append(wrap)}
 const rawDetails=$('raw').closest('details');rawDetails.open=false;$('raw').textContent='';rawDetails.ontoggle=()=>{if(rawDetails.open&&!$('raw').textContent)$('raw').textContent=JSON.stringify(r.raw,null,2)};$('editor').showModal();
}
$('saveRecord').onclick=async()=>{if(editLibrary!=='manual'){try{const fields=Object.fromEntries(FIELDS.map(([key])=>[key,$('edit-'+key).value.trim()]));await poolRequest('/api/pool/edit',{id:editId,fields});await refreshPool(true);$('editor').close();message('修改已保存在常备库中。')}catch(e){message('保存失败：'+e.message)}return}const index=records.findIndex(r=>r.id===editId);if(index<0)return;const r=records[index];for(const[key]of FIELDS){const value=$('edit-'+key).value.trim();if(value!==r[key]){r[key]=value;r.fieldSources[key]='手动编辑'}}records[index]=newRecord({...r,error:''});await persist();render();$('editor').close();message('修改已保存，手动编辑字段会在重新读取时保留。')};
for(const tab of document.querySelectorAll('[data-panel]'))tab.onclick=()=>{for(const b of document.querySelectorAll('[data-panel]')){b.classList.toggle('selected',b===tab);$(b.dataset.panel).hidden=b!==tab}};
function selectPanel(id){document.querySelector(`[data-panel="${id}"]`).click()}
const originPattern=url=>{const u=new URL(url);return u.protocol+'//'+u.hostname+'/*'};
async function originPermission(urls){if(!extended){if(localApi)return true;throw new Error('请使用“启动接口工具.cmd”启动接口版，或在 Chrome / Edge 中安装扩展')}const origins=[...new Set(urls.map(originPattern))];if(!origins.length)return true;return chrome.permissions.request({origins})}
async function extractTab(tabId){await chrome.scripting.executeScript({target:{tabId},files:['public-profile.js']});const results=await chrome.scripting.executeScript({target:{tabId},files:['content.js']});if(!results[0]?.result)throw new Error('页面没有返回可读取内容');return results[0].result;}
async function waitLoaded(tabId,timeout=35000){const started=Date.now();while(Date.now()-started<timeout){if(stopped)throw new Error('已停止');const tab=await chrome.tabs.get(tabId);if(tab.status==='complete')return;await new Promise(r=>setTimeout(r,500))}throw new Error('页面加载超时（35 秒）')}
$('readCurrent').onclick=async()=>{if(!sourceTab)return message('未关联原页面。请在角色页面点击浏览器工具栏中的扩展图标。');running=true;render();message('正在读取原页面公开内容…');try{const tab=await chrome.tabs.get(sourceTab);if(!safeUrl(tab.url))throw new Error('原页面不是可读取的 HTTP / HTTPS 页面');const page=await extractTab(sourceTab);upsert(pageToRecord(page,tab.url));await persist();message('原页面读取完成，缺少的字段已标记。')}catch(e){message('读取失败：'+e.message)}finally{running=false;render()}};
$('start').onclick=async()=>{
 const urls=extractUrls($('urls').value).map(safeUrl).filter(Boolean);if(!urls.length){if(localApi&&poolAvailable)return startAutomation();return message('请先输入有效的 HTTP / HTTPS 角色链接。')}if(urls.length>500)return message('单次最多处理 500 个链接，请分批读取。');
 try{if(!await originPermission([...urls,...apiPermissionUrls(urls)]))return message('未授予这些站点的读取权限，未开始读取。')}catch(e){return message(e.message)}
 running=true;stopped=false;jobController=new AbortController();render();let done=0;const failures=[];
 try{for(const url of urls){if(stopped)break;let tab;message(`正在读取 ${done+1} / ${urls.length}：${new URL(url).hostname}`);
  try{const api=detectApiSource(url);if(api){message(`正在调用 ${API_SOURCES[api.source].label} 接口：${done+1} / ${urls.length}`);const record=await readApiCharacter(url,{fetcher:apiFetch,signal:jobController.signal});if(stopped)break;upsert(record)}else{if(!extended)throw new Error('此链接没有接入公开接口；如需读取可见页面，请安装扩展');tab=await chrome.tabs.create({url,active:false});sessionPort?.postMessage({type:'opened',tabId:tab.id});await waitLoaded(tab.id);if(stopped)break;const current=await chrome.tabs.get(tab.id);if(!current.url)throw new Error('页面跳转到未授权的站点或登录页');if(new URL(current.url).origin!==new URL(url).origin){const allowed=await chrome.permissions.contains({origins:[originPattern(current.url)]});if(!allowed)throw new Error('跳转到未授权的站点：'+new URL(current.url).hostname)}const page=await extractTab(tab.id);if(stopped)break;const record=pageToRecord(page,url);upsert(record);if(record.error)failures.push(url)}
  }catch(e){if(stopped)break;upsert(newRecord({source:url,method:'公开页面',error:e.message}));failures.push(url)}finally{if(tab?.id)try{await chrome.tabs.remove(tab.id);sessionPort?.postMessage({type:'closed',tabId:tab.id})}catch{}}
  done++;await persist();render();if(!stopped)await new Promise(r=>setTimeout(r,1800));
 }}finally{running=false;jobController=null;render();await persist();message(`${stopped?'已停止':'批量读取完成'}：已处理 ${done} / ${urls.length}，失败 ${failures.length}。记录已保存在本机。`)}
};
$('stop').onclick=()=>{stopped=true;jobController?.abort();message('正在停止，将取消接口请求并保留已读取结果…')};
$('apiSearch').onclick=async()=>{
 const source=$('apiSource').value,query=$('apiQuery').value,page=Math.max(1,Math.min(1000,Number($('apiPage').value)||1)),pages=Math.max(1,Math.min(10,Number($('apiPages').value)||1)),sort=$('apiSort').value;
 try{if(!await originPermission([API_SOURCES[source].searchOrigin]))return message('未授予接口站点访问权限。')}catch(e){return message(e.message)}
 running=true;stopped=false;jobController=new AbortController();render();let found=[],total=0,error='';
 try{for(let index=0;index<pages;index++){if(stopped)break;$('apiStatus').textContent=`正在请求 ${API_SOURCES[source].label}，第 ${page+index} 页…`;const result=await searchApi(source,{query,page:page+index,limit:20,sort},{fetcher:apiFetch,signal:jobController.signal});found.push(...result.items.map(n=>n.url));total=result.total;if(!result.nextPage)break;await new Promise(r=>setTimeout(r,1200))}}
 catch(e){if(!stopped)error=e.message}
 finally{const merged=[...new Set([...extractUrls($('urls').value),...found])];$('urls').value=merged.join('\n');running=false;jobController=null;render();const summary=`已从 ${API_SOURCES[source].label} 接口获取 ${new Set(found).size} 条链接，目录返回总数 ${total}。${error?'接口错误：'+error+'。':''}${stopped?'任务已停止。':'点击“手动读取链接”获取完整角色资料。'}`;$('apiStatus').textContent=summary;message(summary);if(found.length)selectPanel('linksPanel')}
};
function parseXml(value){const doc=new DOMParser().parseFromString(value,'application/xml');if(doc.querySelector('parsererror'))throw new Error('XLSX 内含无效 XML');return doc}
async function importXlsx(file){const entries=await readZip(await file.arrayBuffer());let content='';for(const[name,xml]of entries){if(!/sharedStrings\.xml$|worksheets\/sheet\d+\.xml$|worksheets\/_rels\/sheet\d+\.xml\.rels$/.test(name))continue;const doc=parseXml(xml);for(const t of doc.getElementsByTagName('t'))content+='\n'+t.textContent;for(const rel of doc.getElementsByTagName('Relationship'))content+='\n'+(rel.getAttribute('Target')||'');for(const f of doc.getElementsByTagName('f'))content+='\n'+f.textContent}
 const urls=extractUrls(content);if(!urls.length)throw new Error('选题表未找到 HTTP / HTTPS 链接');$('urls').value=[...new Set([...extractUrls($('urls').value),...urls])].join('\n');selectPanel('linksPanel');return `已从选题表导入 ${urls.length} 个链接，点击“手动读取链接”继续。`;
}
function dataUrl(bytes,type){const chunks=[];for(let i=0;i<bytes.length;i+=8192)chunks.push(String.fromCharCode(...bytes.slice(i,i+8192)));return `data:${type};base64,${btoa(chunks.join(''))}`}
function parseJsonRecords(value,source){if(Array.isArray(value))return value.flatMap(v=>parseJsonRecords(v,source));if(value?.records&&Array.isArray(value.records))return value.records.map(r=>r.fieldSources&&r.id?newRecord(r):cardToRecord(r,source));return [cardToRecord(value,source)]}
async function importFiles(files){if(running)return;let count=0,errors=[],messages=[];for(const file of files){try{if(file.size>30*1024*1024)throw new Error('文件超过 30 MB');const ext=file.name.split('.').pop().toLowerCase();if(ext==='xlsx'){messages.push(await importXlsx(file));continue}if(ext==='txt'){const urls=extractUrls(await file.text());$('urls').value=[...new Set([...extractUrls($('urls').value),...urls])].join('\n');messages.push(`已导入 ${urls.length} 个链接。`);selectPanel('linksPanel');continue}
 let imported;if(ext==='png'){const bytes=new Uint8Array(await file.arrayBuffer());imported=[cardToRecord(readPngCard(bytes),file.name,dataUrl(bytes,'image/png'))]}else if(ext==='json')imported=parseJsonRecords(JSON.parse(await file.text()),file.name);else throw new Error('不支持此文件格式');for(const record of imported){upsert(record);count++}
 }catch(e){errors.push(file.name+'：'+e.message)}}await persist();render();message([count?`已导入 ${count} 条角色资料。`:'',...messages,...errors].filter(Boolean).join(' '));$('files').value=''}
$('files').onchange=e=>importFiles([...e.target.files]);for(const event of ['dragenter','dragover'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.add('dragging')});for(const event of ['dragleave','drop'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.remove('dragging');if(event==='drop')importFiles([...e.dataTransfer.files])});
$('search').oninput=render;$('filter').onchange=render;$('recordOrder').onchange=render;$('sourceRatingFilter').onchange=render;$('clear').onclick=async()=>{if(!records.length)return;if(!confirm('清空本机保存的全部角色记录？请先导出需要保留的资料。'))return;records=[];await persist();render();message('已清空本机记录。')};
async function getAvatar(r,index){const url=r.avatar;if(!url)return null;let blob;if(r.avatarVisibility==='covered'){const img=new Image();img.src=url;await img.decode();const canvas=document.createElement('canvas');canvas.width=320;canvas.height=400;canvas.getContext('2d').drawImage(img,0,0);blob=await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('安全封面导出失败')),'image/png'))}else if(/^data:image\/(png|jpeg|webp|gif);base64,/i.test(url)){blob=await(await fetch(url)).blob()}else{if(!safeUrl(url))throw new Error('无效的头像 URL');const response=await (localApi&&r.addedAt?fetch('/api/pool/avatar?id='+encodeURIComponent(r.id),{signal:AbortSignal.timeout(20000)}):(localApi&&detectApiSource(url)?apiFetch:fetch)(url,{credentials:'omit',signal:AbortSignal.timeout(15000)}));if(!response.ok)throw new Error('HTTP '+response.status);if(!/^image\//i.test(response.headers.get('content-type')||''))throw new Error('返回内容不是图片');const length=Number(response.headers.get('content-length'));if(length>15*1024*1024)throw new Error('头像超过 15 MB');blob=await response.blob()}
 if(blob.size>15*1024*1024)throw new Error('头像超过 15 MB');const ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp','image/gif':'gif'}[blob.type];if(!ext)throw new Error('不支持的图片格式：'+blob.type);return {name:`avatars/${String(index+1).padStart(4,'0')}.${ext}`,data:new Uint8Array(await blob.arrayBuffer())};}
function download(bytes,name){const url=URL.createObjectURL(new Blob([bytes],{type:'application/zip'})),a=element('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000)}
async function exportRecords(selected){if(!selected.length)return;const assets=[],avatarMap={},avatarErrors=[];
 if($('withAvatars').checked&&extended){const urls=selected.map(r=>safeUrl(r.avatar)).filter(Boolean);if(urls.length){try{if(!await originPermission(urls))return message('未授予头像域名权限；可取消“下载头像”后导出。')}catch(e){return message('头像权限请求失败：'+e.message)}}}
 running=true;render();try{if($('withAvatars').checked){for(const[i,r]of selected.entries()){message(`正在准备头像 ${i+1} / ${selected.length}…`);try{const asset=await getAvatar(r,i);if(asset){assets.push(asset);avatarMap[r.id]=asset.name}}catch(e){avatarErrors.push({id:r.id,url:r.avatar,error:e.message})}}}
 const exported=selected.map(r=>({...r,avatarFile:avatarMap[r.id]||''}));
 const metricKeys=['views','favorites','downloads','chats','messages','rating','ratingCount'];const headers=['记录ID',...FIELDS.map(([,v])=>v),'头像文件','作者','来源','读取方式','缺失字段','读取错误','读取时间','备注','原站分级','浏览数','收藏数','下载数','聊天数','消息数','评分','评分人数','热度数据时间'];
 const rows=exported.map(r=>[r.id,...FIELDS.map(([k])=>k==='avatar'&&r.avatar.startsWith('data:')?(r.avatarFile||'图片已保存在完整 JSON 的 avatar 字段'):r[k]),r.avatarFile,r.creator,r.source,r.method,r.missing.join('、'),r.error,r.readAt,r.notes,r.sourceRating||'unknown',...metricKeys.map(k=>r.sourceMetrics?.[k]??''),r.metricsAt||'']);
 const json={schemaVersion:1,library:libraryView,exportedAt:new Date().toISOString(),records:exported};const failures=exported.filter(r=>r.error).map(r=>({id:r.id,source:r.source,error:r.error}));
 const readme='角色资料读取器导出结果\n\n角色资料.xlsx：九项角色字段及来源、缺失项；超过 Excel 单元格长度的内容分段保存在“长字段”。\n角色资料.json：完整记录、原始页面或角色卡、字段来源及所有扩展信息。\n失败清单.json：页面读取及头像下载失败信息。\n空白字段代表未公开或未识别，不代表作者没有设定。手动编辑字段在 fieldSources 中标记。\n导入角色卡包含的人设优先按原始文本保留。页面读取只读取可见内容及公开元数据。\n';
 const archive=makeZip([{name:'角色资料.xlsx',data:makeXlsx(headers,rows)},{name:'角色资料.json',data:JSON.stringify(json,null,2)},{name:'失败清单.json',data:JSON.stringify({pages:failures,avatars:avatarErrors},null,2)},{name:'说明.txt',data:readme},...assets]);
 download(archive,'角色资料_'+new Date().toISOString().slice(0,10)+'.zip');message(`已导出 ${selected.length} 条记录、${assets.length} 张头像；导出不消耗库存。${avatarErrors.length?'有 '+avatarErrors.length+' 个头像下载失败，原因已写入失败清单。':''}`);
 }catch(e){message('导出失败：'+e.message)}finally{running=false;render()}};
$('export').onclick=()=>exportRecords(currentVisible().map(publicRecord));
$('exportOne').onclick=()=>{const list=editLibrary==='manual'?records:editLibrary==='batch'?batchRecords:editLibrary==='used'?poolState?.history||[]:poolState?.ready||[];const record=list.find(r=>r.id===editId);if(record)exportRecords([publicRecord(record)])};
try{const saved=extended?(await chrome.storage.local.get(storageKey))[storageKey]:JSON.parse(localStorage.getItem(storageKey)||'[]');records=Array.isArray(saved)?saved.map(newRecord):[]}catch(e){message('无法载入历史记录：'+e.message)}
$('environment').hidden=extended;render();
if(!extended&&['127.0.0.1','localhost'].includes(location.hostname)){try{const response=await fetch('/api/status',{signal:AbortSignal.timeout(2000)});const status=response.ok?await response.json():{};if(status.roleReaderApi){localApi=true;$('environment').textContent='接口版已连接：Chub、CharaVault、BotBooru 的链接将直接读取公开接口，无需安装扩展。';if(status.pool){poolAvailable=true;libraryView='pool';$('poolPanel').hidden=false;$('libraryTabs').hidden=false;$('pageTitle').textContent='随时备好 50 个 bot。';$('pageDescription').textContent='点击“开始批量读取”自动找新角色；点击“已使用”，新 bot 自动补位。';await refreshPool(true);await initAutomation();setInterval(()=>refreshPool(),4000);message('常备库已连接，使用记录和库存会自动保存。')}else message('接口已就绪。可在“接口资源”中获取链接，或直接粘贴三站角色链接。');render()}}catch{}}
