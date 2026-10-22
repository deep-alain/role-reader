const count=value=>Number.isFinite(Number(value))?Math.max(0,Math.floor(Number(value))):0;
const names={chub:'Chub',charavault:'CharaVault',botbooru:'BotBooru',crushon:'CrushOn',spicychat:'SpicyChat'};
const statuses={idle:'尚未开始',running:'正在批量读取',completed:'本轮读取完成',cancelling:'正在停止本轮读取',cancelled:'已停止本轮读取',waitingReview:'读取完成，等待头像核验',failed:'本轮读取未完成',interrupted:'上次读取已中断'};
const sourceStatuses={idle:'待开始',queued:'排队中',running:'读取中',reading:'读取中',completed:'已完成',error:'暂时不可用',failed:'暂时不可用',cancelled:'已停止',blocked:'访问受限',login_required:'需要登录授权',authorization_required:'完整资料需要授权',metadata_only:'仅候选目录',partial:'部分请求失败，其他继续',waitingReview:'有疑难项保留'};
const stages={catalogue:'正在搜索跨站目录',discovery:'正在读取其他渠道的候选目录',read:'正在读取完整角色资料',review:'正在核验头像及内容',preparing_models:'正在准备本机自动核验模型',finished:'本轮处理已结束',cancelled:'已停止本轮处理'};
const reasons={duplicate:'重复角色',lowHeat:'热度不足',age:'成年设定不符',image:'头像不符',policy:'内容筛选未通过'};
function configuredSources(sources){
 if(Array.isArray(sources))return sources.map(source=>typeof source==='string'?{id:source}:{...source,id:source.id||source.key||source.source});
 return Object.entries(sources||{}).map(([id,source])=>typeof source==='object'?{...source,id}:{id,label:source});
}
export function automationView(snapshot={}){
 const job=snapshot.job||{},active=job.active===true||job.status==='running',reviewer=snapshot.reviewer||{available:job.reviewerAvailable,note:job.reviewReason};
 const read=count(job.read),accepted=count(job.accepted),readyAdded=count(job.readyAdded),pending=count(job.pending),reviewed=count(job.reviewed),rejected=count(job.rejected);
 const configured=configuredSources(snapshot.sources),sourceIds=new Set([...configured.map(source=>source.id).filter(Boolean),...Object.keys(job.perSource||{})]);
 const sources=[...sourceIds].map(id=>{
  const source=configured.find(value=>value.id===id)||{},stats=job.perSource?.[id]||{};
  const metadataOnly=source.metadataOnly===true||source.ingest===false||source.fullDefinition===false||stats.metadataOnly===true||source.mode==='metadata'||stats.mode==='metadata';
  const label=source.label||source.name||stats.label||names[id]||id;
  const sourceStatus=stats.errors>0&&stats.readSucceeded>0?'partial':stats.status;
  const status=sourceStatuses[sourceStatus]||sourceStatus||(metadataOnly?'仅候选目录':'完整角色读取');
  const detail=metadataOnly?`发现候选 ${count(stats.discovered)} 条；只获取候选目录，不计入完整角色或入库数`:`请求 ${count(stats.read)} · 实读 ${count(stats.readSucceeded??stats.read)} · 通过 ${count(stats.accepted)} · 待核验 ${count(stats.pending)} · 淘汰 ${count(stats.rejected)}`;
  return {id,label,status,detail,error:String(stats.error||stats.note||source.note||'')};
 });
 const reviewerNote=reviewer.available===true?`头像自动核验已连接${reviewer.backend?'（'+(reviewer.backend==='local-onnx-cpu'?'本机图像模型':reviewer.backend)+'）':''}。${reviewer.note||''}`:reviewer.note||'尚未配置头像自动核验；符合文字条件的角色会进入待核验，核验通过后才入库。';
 const summary=read||job.id?`已请求 ${read} 条，取得完整资料 ${count(job.readSucceeded??read)} 条；审核通过 ${accepted} 条（其中本轮进入待用／备用 ${readyAdded} 条，其余可供后续补位，包含本轮通过的已有候选）；当前待核验 ${pending} 条；本轮已核验 ${reviewed} 条；淘汰 ${rejected} 条。`:'一键搜索已接通的来源、读取完整角色资料、检查成年设定与热度、去重并核验头像，合格角色自动入库或等待补位。';
 const reasonText=Object.entries(job.reasonCounts||{}).filter(([,value])=>count(value)>0).map(([reason,value])=>`${reasons[reason]||reason} ${count(value)}`).join('；');
 const stage=job.stage||job.currentStage||'';
 return {jobId:job.id||'',active,title:statuses[job.status]||'尚未开始',summary,stage:[stages[stage]||String(stage),job.currentSource?(names[job.currentSource]||job.currentSource):'',job.message||''].filter(Boolean).join(' · '),reviewerNote,sources,reasons:reasonText,read,accepted,readyAdded,pending};
}

// This controller only updates progress; role rows and open editors stay intact.
export function automationController({get,post,render,schedule=setTimeout,unschedule=clearTimeout}){
 let snapshot={},timer=null,fetching=false,mutating=false,generation=0,disposed=false,lastError='';
 const draw=()=>render(automationView(snapshot),{busy:mutating,error:lastError});
 function plan(){if(disposed)return;if(timer!=null)unschedule(timer);timer=schedule(()=>{timer=null;refresh()},automationView(snapshot).active?2000:15000)}
 async function refresh(){
  if(disposed||fetching||mutating)return;
  fetching=true;const token=generation;
  try{const next=await get();if(!disposed&&token===generation){snapshot=next;lastError='';draw()}}
  catch(error){if(!disposed&&token===generation){lastError='读取进度连接中断：'+error.message+'。恢复连接后会继续显示。';draw()}}
  finally{fetching=false;plan()}
 }
 async function change(path,body){
  if(disposed||mutating||path.endsWith('/start')&&automationView(snapshot).active)return false;
  mutating=true;generation++;if(timer!=null)unschedule(timer);timer=null;lastError='';draw();
  try{const next=await post(path,body);if(!disposed){snapshot=next;draw()}return true}
  catch(error){if(!disposed){lastError=error.message;draw()}return false}
  finally{mutating=false;if(!disposed){draw();plan()}}
 }
 return {refresh,start:()=>change('/api/automation/start',{maxRead:120}),cancel:()=>change('/api/automation/cancel',{}),dispose(){disposed=true;generation++;if(timer!=null)unschedule(timer)}};
}
