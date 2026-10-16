import {cardToRecord,newRecord,safeUrl,text} from './core.js';
import {readPngCard} from './zip.js';

export const API_SOURCES=Object.freeze({
 chub:{label:'Chub',origin:'https://api.chub.ai',searchOrigin:'https://gateway.chub.ai'},
 charavault:{label:'CharaVault',origin:'https://charavault.net',searchOrigin:'https://charavault.net'},
 botbooru:{label:'BotBooru',origin:'https://botbooru.com',searchOrigin:'https://botbooru.com'}
});
function segments(url){return new URL(url).pathname.split('/').filter(Boolean).map(p=>decodeURIComponent(p))}
function validParts(parts){return parts.length>0&&parts.every(p=>p&&p!=='.'&&p!=='..'&&!/[\/\\\u0000-\u001f]/.test(p))}
export function detectApiSource(value){
 if(!safeUrl(value))return null;const u=new URL(value),host=u.hostname.replace(/^www\./,'').toLowerCase();let p;try{p=segments(value)}catch{return null}
 if(['chub.ai','chat.chub.ai','characterhub.org','api.chub.ai','gateway.chub.ai'].includes(host)){
  if(p[0]==='api')p.shift();if(p[0]==='characters')p.shift();if(p.length!==2||!validParts(p))return null;
  return {source:'chub',id:p.join('/'),endpoint:'https://api.chub.ai/api/characters/'+p.map(encodeURIComponent).join('/')+'?full=true'};
 }
 if(host==='charavault.net'){
  if(p.slice(0,3).join('/')==='api/cards/download')p=p.slice(3);else if(['cards','card'].includes(p[0]))p=p.slice(1);else return null;
  if(p.length!==2||!validParts(p))return null;
  return {source:'charavault',id:p.join('/'),endpoint:'https://charavault.net/api/cards/download/'+p.map(encodeURIComponent).join('/')};
 }
 if(host==='botbooru.com'){
  const id=(p.length===2&&['character','post'].includes(p[0])?p[1]:p.length===3&&p[0]==='download'&&['png','json'].includes(p[1])?p[2]:'');
  if(!/^\d{1,12}$/.test(id))return null;
  return {source:'botbooru',id,endpoint:'https://botbooru.com/download/png/'+id};
 }return null;
}
export function apiPermissionUrls(urls){return [...new Set(urls.flatMap(url=>{const match=detectApiSource(url);return match?[match.endpoint]:[]}))]}
export function buildSearchUrl(source,{query='',page=1,limit=20,sort='popular'}={}){
 if(!API_SOURCES[source])throw new Error('未知接口来源');
 page=Math.max(1,Math.min(1000,Number(page)||1));limit=Math.max(1,Math.min(48,Number(limit)||20));query=String(query).slice(0,128);
 let u;
 const order={chub:{popular:'n_favorites',views:'download_count',favorites:'n_favorites',latest:'last_activity_at'},charavault:{popular:'most_downloaded',views:'most_downloaded',favorites:'top_rated',latest:'newest'},botbooru:{popular:'views',views:'views',favorites:'favorites',latest:'latest'}}[source][sort];if(!order)throw new Error('未知排序方式');
 if(source==='chub'){u=new URL('/search',API_SOURCES.chub.searchOrigin);for(const[k,v]of Object.entries({search:query,namespace:'characters',first:limit,page,sort:order,asc:false,nsfw:false,nsfl:false,nsfw_only:false,include_forks:true,count:true}))u.searchParams.set(k,v)}
 if(source==='charavault'){u=new URL('/api/cards',API_SOURCES.charavault.origin);for(const[k,v]of Object.entries({q:query,limit,offset:(page-1)*limit,sort:order,nsfw:false}))u.searchParams.set(k,v)}
 if(source==='botbooru'){u=new URL('/posts/',API_SOURCES.botbooru.origin);for(const[k,v]of Object.entries({q:'',qtext:query,limit,offset:(page-1)*limit,sort:order,time_window:'all'}))u.searchParams.set(k,v)}
 return u.href;
}
export function sourceMetadata(source,node){
 const fields={chub:{favorites:'n_favorites',downloads:'starCount',chats:'nChats',messages:'nMessages',rating:'rating',ratingCount:'ratingCount'},botbooru:{views:'views',favorites:'favorite_count',downloads:'downloads',comments:'comments_count'},charavault:{rating:'avg_rating',ratingCount:'rating_count',comments:'comment_count'}}[source];
 const sourceMetrics=Object.fromEntries(Object.entries(fields).map(([k,f])=>[k,typeof node[f]==='number'&&Number.isFinite(node[f])&&node[f]>=0?node[f]:null]));
 const tags=(node.topics||node.tags||[]).map(t=>String(typeof t==='string'?t:t.name||'').trim().toLowerCase().replace(/^meta:/,''));
 const sourceRating=node.is_nsfl===true||tags.includes('nsfl')?'NSFL':node.nsfw===true||node.is_nsfw===true||tags.includes('nsfw')?'NSFW':node.nsfw===false||node.is_nsfw===false||tags.includes('sfw')||source==='chub'&&Array.isArray(node.topics)?'SFW':'unknown';
 return {sourceMetrics,sourceRating,metricsSource:source,metricsAt:new Date().toISOString()};
}
async function boundedFetch(url,{fetcher=globalThis.fetch,signal,kind='json'}={}){
 const timed=AbortSignal.timeout(25000),combined=signal?AbortSignal.any([signal,timed]):timed;
 const response=await fetcher(url,{method:'GET',credentials:'omit',redirect:'error',headers:{Accept:kind==='json'?'application/json':'image/png,application/octet-stream;q=0.9'},signal:combined});
 if(!response.ok)throw new Error(`公开接口返回 HTTP ${response.status}`);
 const max=kind==='json'?12*1024*1024:30*1024*1024,declared=Number(response.headers.get('content-length'));
 if(declared>max)throw new Error('接口返回超过大小上限');
 let bytes;if(response.body?.getReader){const reader=response.body.getReader(),chunks=[];let size=0;while(true){const next=await reader.read();if(next.done)break;size+=next.value.length;if(size>max){await reader.cancel();throw new Error('接口返回超过大小上限')}chunks.push(next.value)}bytes=new Uint8Array(size);let offset=0;for(const b of chunks){bytes.set(b,offset);offset+=b.length}}else{bytes=new Uint8Array(await response.arrayBuffer());if(bytes.length>max)throw new Error('接口返回超过大小上限')}
 if(kind==='json'){try{return JSON.parse(new TextDecoder().decode(bytes))}catch{throw new Error('接口未返回有效 JSON，可能需要登录或访问验证')}}return bytes;
}
export function chubToRecord(response,source,endpoint){
 const n=response?.node,d=n?.definition;
 if(!n||!d||typeof d!=='object')throw new Error('Chub 详情没有提供 definition；不能用列表简介代替人设');
 if(n.is_public===false||d.is_public===false)throw new Error('此角色或定义未公开');
 const card={spec:'chara_card_v2',spec_version:'2.0',data:{name:d.name||n.name,description:d.personality||'',personality:d.tavern_personality||'',scenario:d.scenario||'',first_mes:d.first_message||'',mes_example:d.example_dialogs||'',creator_notes:d.description||n.description||'',system_prompt:d.system_prompt||'',post_history_instructions:d.post_history_instructions||'',alternate_greetings:d.alternate_greetings||[],tags:n.topics||[],creator:text(n.fullPath||d.full_path).split('/')[0],character_book:d.embedded_lorebook??null,extensions:d.extensions||{}}};
 const record=cardToRecord(card,source,n.max_res_url||n.avatar_url||'');record.method='Chub 公开接口';record.raw={apiEndpoint:endpoint,apiResponse:response,card};
 record.notes='通过 full=true 详情接口读取作者公开定义；空字段不补写。完整接口响应、备用开场白和世界书均保留。';
 for(const[key]of Object.entries(record.fieldSources))record.fieldSources[key]='Chub 公开详情接口';
 return newRecord({...record,...sourceMetadata('chub',n)});
}
export async function readApiCharacter(url,options={}){
 const match=detectApiSource(url);if(!match)throw new Error('此链接没有已接入的公开接口');
 if(match.source==='chub')return chubToRecord(await boundedFetch(match.endpoint,options),url,match.endpoint);
 const bytes=await boundedFetch(match.endpoint,{...options,kind:'binary'}),card=readPngCard(bytes),r=cardToRecord(card,url,match.endpoint);
 r.method=API_SOURCES[match.source].label+' 角色卡下载接口';r.raw={apiEndpoint:match.endpoint,downloadBytes:bytes.length,card};r.notes='从公开下载接口获取 PNG，并解析其中的原始角色卡元数据；不从页面简介重建人设。';
 if(match.source==='botbooru'){const metadataEndpoint='https://botbooru.com/post/'+match.id;try{r.raw.metadataEndpoint=metadataEndpoint;r.raw.apiResponse=await boundedFetch(metadataEndpoint,options);Object.assign(r,sourceMetadata('botbooru',r.raw.apiResponse.post||r.raw.apiResponse))}catch(e){if(options.signal?.aborted)throw e;r.raw.metadataError=e.message;r.notes+=' 附加元数据读取失败，角色卡数据仍保留。'}}
 for(const[key]of Object.entries(r.fieldSources))r.fieldSources[key]=API_SOURCES[match.source].label+' PNG 角色卡元数据';
 return newRecord(r);
}
export function parseSearchResponse(source,response,{page=1,limit=20}={}){
 let raw=[],total=0,items=[];
 if(source==='chub'){if(!Array.isArray(response?.data?.nodes))throw new Error('Chub 列表缺少 data.nodes');raw=response.data.nodes;total=Number(response.data.count)||0;items=raw.map(n=>({name:text(n.name),url:'https://chub.ai/characters/'+text(n.fullPath).split('/').map(encodeURIComponent).join('/'),...sourceMetadata(source,n)})).filter(n=>detectApiSource(n.url));}
 if(source==='charavault'){if(!Array.isArray(response?.results))throw new Error('CharaVault 列表缺少 results');raw=response.results;total=Number(response.total)||0;items=raw.map(n=>({name:text(n.name),url:'https://charavault.net/cards/'+[n.folder,n.file].map(x=>encodeURIComponent(text(x))).join('/'),...sourceMetadata(source,n)})).filter(n=>detectApiSource(n.url));}
 if(source==='botbooru'){if(!Array.isArray(response?.posts))throw new Error('BotBooru 列表缺少 posts');raw=response.posts;total=Number(response.total)||0;items=raw.map(n=>({name:text(n.character_name)||'角色 '+n.id,url:'https://botbooru.com/character/'+n.id,...sourceMetadata(source,n)})).filter(n=>detectApiSource(n.url));}
 return {items,total,page,nextPage:raw.length&&(!total?raw.length>=limit:page*limit<total)?page+1:null};
}
export async function searchApi(source,params={},options={}){const endpoint=buildSearchUrl(source,params),response=await boundedFetch(endpoint,options),result=parseSearchResponse(source,response,params);const metric=source==='charavault'?(params.sort==='favorites'?'rating':null):params.sort==='favorites'||source==='chub'&&(!params.sort||params.sort==='popular')?'favorites':source==='chub'?'downloads':'views';if(metric&&params.sort!=='latest')result.items.sort((a,b)=>(b.sourceMetrics?.[metric]??-1)-(a.sourceMetrics?.[metric]??-1));return {...result,endpoint};}
