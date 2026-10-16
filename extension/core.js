export const FIELDS = [
  ['category','角色类别'],['name','角色名称'],['avatar','角色头像'],['gender','性别'],['age','角色年龄'],
  ['scenario','Scenario'],['greet','角色greet'],['intro','角色介绍'],['prompt','角色prompt']
];
const plain = value => typeof value === 'string' ? value.trim() : typeof value === 'number' ? String(value) : '';
export function text(value) { return Array.isArray(value) ? value.map(v=>plain(v)||plain(v?.name)||plain(v?.label)).filter(Boolean).join(', ') : plain(value); }
export function extractUrls(value) {
  return [...new Set((String(value).match(/https?:\/\/[^\s<>"'，。；）\]）]+/gi)||[]).map(v=>v.replace(/[),;]+$/,'')))];
}
export function safeUrl(value) {
  try {const u=new URL(value); return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';} catch {return '';}
}
export function normalizeGender(value) {
  const s=text(value).toLowerCase().replace(/[_\s]+/g,'-');
  if (['male','man','boy','m','男性','男','男生','男朋友'].includes(s)) return 'Male';
  if (['female','woman','girl','f','女性','女','女生','女朋友'].includes(s)) return 'Female';
  if (['non-binary','nonbinary','nb','enby','非二元','非二元性别'].includes(s)) return 'Non-binary';
  return '';
}
export function explicitDemographics(value) {
  const s=text(value);
  const age=s.match(/(?:\bage\b|年龄|年齡)\s*[：:=\-(]\s*(\d{1,4})(?:\s*(?:years?\s*old|岁|歳))?/i)
    ||s.match(/\b(\d{1,4})\s*(?:years?[- ]old|y\/?o)\b/i)
    ||s.match(/(\d{1,4})\s*(?:岁|歳)/);
  const g=s.match(/(?:\bgender\b|性别|性別)\s*[：:=\-(]\s*(non[- ]?binary|female|male|非二元性别|非二元|女性|男性|女|男)(?=\s|[.,;，。；)\]]|$)/i);
  return {age:age?.[1]||'',gender:g?normalizeGender(g[1]):''};
}
export function newRecord(values={}) {
  const r={id:globalThis.crypto?.randomUUID?.()||'r-'+Date.now()+'-'+Math.random().toString(16).slice(2),...Object.fromEntries(FIELDS.map(([k])=>[k,''])),source:'',creator:'',notes:'',method:'',readAt:new Date().toISOString(),error:'',fieldSources:{},raw:{},...values};
  for(const [key] of FIELDS) r[key]=text(r[key]);
  for(const key of ['id','source','creator','notes','method','readAt','error']) r[key]=text(r[key]);
  if(!r.fieldSources||typeof r.fieldSources!=='object'||Array.isArray(r.fieldSources))r.fieldSources={};
  r.gender=normalizeGender(r.gender); r.missing=FIELDS.filter(([k])=>!text(r[k])).map(([,label])=>label);
  r.status=r.error?'failed':r.missing.length?'partial':'complete'; return r;
}
export function cardToRecord(card, source='', avatar='') {
  if (!card || typeof card!=='object' || Array.isArray(card)) throw new Error('角色卡必须是 JSON 对象');
  const d=card.data&&typeof card.data==='object'?card.data:card.character&&typeof card.character==='object'?card.character:card;
  if (!['name','char_name','character_name','description','personality','first_mes','first_message','char_greeting','scenario','角色名称'].some(k=>d[k]!=null)) throw new Error('未识别到角色卡字段');
  const extensions=d.extensions||{}, meta=d.metadata||{}, demographic=explicitDemographics([text(d.description),text(d.personality)].join('\n'));
  const prompts=[['Description',d.description||d.char_persona],['Personality',d.personality],['System prompt',d.system_prompt],['Post-history instructions',d.post_history_instructions],['Example dialogue',d.mes_example||d.example_dialogue]]
    .filter(([,v])=>text(v)).map(([k,v])=>`[${k}]\n${text(v)}`).join('\n\n');
  const avatarValue=avatar||text(d.avatar||d.avatar_url||d.image_url||d.imageUrl||d['角色头像']);
  const record=newRecord({category:text(d.category||d.tags||d['角色类别']),name:text(d.name||d.char_name||d.character_name||d['角色名称']),avatar:/^(none|null|undefined)$/i.test(avatarValue)?'':avatarValue,
    gender:normalizeGender(d.gender||extensions.gender||meta.gender||d['性别'])||demographic.gender,
    age:text(d.age||extensions.age||meta.age||d['角色年龄'])||demographic.age,
    scenario:text(d.scenario||d.world_scenario||d['Scenario']),greet:text(d.first_mes||d.first_message||d.initial_message||d.firstMessage||d.char_greeting||d.greeting||d['角色greet']),
    intro:text(d.creator_notes||d.description||d.char_persona||d['角色介绍']),prompt:prompts||text(d.prompt||d['角色prompt']),
    creator:text(d.creator||meta.creator),source,method:'本地角色卡',raw:card,notes:'保留完整角色卡数据。年龄和性别仅读取显式字段或明确标签，不按姓名、头像推断。'});
  for(const [key] of FIELDS) if(record[key]) record.fieldSources[key]=key==='avatar'&&avatar?'导入的 PNG 图片':'角色卡字段';
  return record;
}
const LABELS={category:['角色类别','类别','分类','Tags','Category'],name:['角色名称','Name'],gender:['性别','Gender'],age:['角色年龄','年龄','Age'],scenario:['Scenario','场景','情境'],greet:['角色greet','Greeting','First message','Initial message','开场白','第一条消息'],intro:['角色介绍','Character bio','About this character','Description','角色描述','介绍'],prompt:['角色prompt','Character definition','Character personality','Personality','System prompt','角色定义','人设','性格']};
export function labelKey(label) {
  const t=String(label||'').trim().replace(/[:：]$/,'').toLowerCase();
  return Object.keys(LABELS).find(k=>LABELS[k].some(v=>v.toLowerCase()===t));
}
export function pageToRecord(page, source='') {
  const fields={}; const provenance={};
  for(const section of page.sections||[]) {const key=labelKey(section.label); if(key&&text(section.value)) {fields[key]=[fields[key],text(section.value)].filter(Boolean).join('\n\n'); provenance[key]='可见页面标签：'+section.label;}}
  if (!fields.name && page.name) {fields.name=page.name;provenance.name='页面标题';}
  if (!fields.intro && page.description) {fields.intro=page.description;provenance.intro='页面公开 description 元数据';}
  fields.avatar=page.avatar||''; if(fields.avatar) provenance.avatar='页面头像 / og:image';
  if(!fields.category&&page.tags?.length){fields.category=page.tags.join(', ');provenance.category='页面公开标签';}
  const dem=explicitDemographics([fields.prompt,fields.intro,fields.age?'Age: '+fields.age:'',fields.gender?'Gender: '+fields.gender:''].filter(Boolean).join('\n'));
  fields.gender=normalizeGender(fields.gender)||dem.gender; fields.age=fields.age||dem.age;
  const record=newRecord({...fields,source,creator:page.creator||'',method:'公开页面',fieldSources:provenance,raw:page,error:page.error||'',notes:page.notes||'页面未公开的字段不读取；全部可见文本保留在原始数据中。'});
  if(!record.error && !record.name&&!record.intro&&!record.greet&&!record.prompt) return newRecord({...record,error:'未识别角色资料；可能需要登录、展开公开区块，或使用角色卡导入。'});
  return record;
}
export function dedupeKey(r) {if(safeUrl(r.source)){const u=new URL(r.source);for(const key of [...u.searchParams.keys()])if(/^utm_|^(from|ref|referrer)$/i.test(key))u.searchParams.delete(key);u.hash='';return u.href.replace(/\/$/,'')}return r.source+'|'+r.name;}
export function publicRecord(r) {return Object.fromEntries(Object.entries(r).filter(([k])=>!k.startsWith('_')));}
