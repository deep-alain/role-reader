"""Durable 50-ready + 5-reserve shelf. Consumption is atomic and idempotent."""
import concurrent.futures,copy,hashlib,json,os,re,threading,time
from pathlib import Path
from pool_sources import FIELDS,SOURCES,normalize_record,now_iso
from pool_policy import PROFILE_ID,PROFILE_LABEL,evaluate
from pool_avatars import fetch_record_avatar,cache_avatar
from duplicate_guard import duplicate_reason

def fingerprint(record):
 content='\n'.join(re.sub(r'\s+',' ',str(record.get(k,'')).strip()).casefold() for k in ('name','prompt','greet'))
 return hashlib.sha256(content.encode('utf-8')).hexdigest()

class PoolStore:
 def __init__(self,directory,client,target=50,reserve_target=5,taste=None):
  from directory_lock import DirectoryLock
  self.writer_lock=DirectoryLock(directory)
  try:self._initialize(directory,client,target,reserve_target,taste)
  except BaseException:self.writer_lock.close();raise
 def _initialize(self,directory,client,target,reserve_target,taste):
  self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True);self.path=self.directory/'pool.json';self.client=client;self.collection_lock=threading.RLock();self.collection_paused=threading.Event()
  self.target=target;self.reserve_target=reserve_target;self.sources=tuple(getattr(client,"sources",SOURCES));self.taste=taste() if callable(taste) else taste;self.lock=threading.RLock();self.wake=threading.Event();self.stop_event=threading.Event();self.thread=None;self.busy=False;self.pending=set();self.source_failures={};self.last_batch_attempted=0
  defaults={'version':1,'revision':0,'profile':'','retired':[],'ready':[],'reserve':[],'history':[],'seenKeys':[],'seenContent':[],'pages':{s:1 for s in self.sources},'queues':{s:[] for s in self.sources},'sourceTurn':0,'failures':{},'lastAddedAt':'','lastHeadCheck':0,'status':'等待自动补货'}
  if self.path.exists():
   saved=json.loads(self.path.read_text(encoding='utf-8'))
   if not isinstance(saved,dict) or any(not isinstance(saved.get(k),list) for k in ('ready','reserve','history')):raise ValueError('库存文件结构无效，已保留原文件，请勿覆盖')
   defaults.update(saved)
  self.state=defaults;self.seen_keys=set(defaults['seenKeys']);self.seen_content=set(defaults['seenContent'])
  for r in self.state['ready']+self.state['reserve']+self.state['history']:
   self.seen_keys.add(r['source']);self.seen_content.add(fingerprint(r))
  for group in ('ready','reserve'):
   kept=[]
   for record in self.state[group]:
    accepted,reason,selected=evaluate(record)
    if accepted:kept.append(normalize_record(selected))
    else:self.state['retired'].append({**record,'retiredReason':reason})
   self.state[group]=kept
  if self.state.get('profile')!=PROFILE_ID:
   self.state['profile']=PROFILE_ID;self.state['pages']={s:1 for s in self.sources};self.state['queues']={s:[] for s in self.sources};self.state['lastHeadCheck']=0
  if self.state.get('cataloguePolicy')!='public-nsfw-mix-v2':
   self.state['cataloguePolicy']='public-nsfw-mix-v2';self.state['pages']={s:1 for s in self.sources};self.state['queues']={s:[] for s in self.sources};self.state['preferenceQueryPages']={};self.state['lastHeadCheck']=0
  for source in self.sources:self.state['pages'].setdefault(source,1);self.state['queues'].setdefault(source,[])
  if self.taste:
   if self.taste.hydrate_feedback([r for group in ('ready','reserve','history','portraitPending','portraitDeferred','retired') for r in self.state.get(group,[])]):self.state['lastHeadCheck']=0
   self.apply_preferences()
  self.dedupe_inventory()
  with self.lock:self._promote();self._save()
 def _save(self):
  if self.writer_lock.file is None:raise ValueError('库存已关闭')
  self.state['revision']+=1
  self.state['seenKeys']=sorted(self.seen_keys);self.state['seenContent']=sorted(self.seen_content)
  tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.state,ensure_ascii=False,separators=(',',':')),encoding='utf-8');os.replace(tmp,self.path)
 def touch_view(self):
  with self.lock:self._save()
 def _visual(self,record):
  asset=record.get('avatarAsset') or {}
  from duplicate_guard import SIGNATURE_VERSION
  if asset and (asset.get('visualSignature') or {}).get('version',0)<SIGNATURE_VERSION:
   from pool_avatars import avatar_file
   from duplicate_guard import image_signature
   try:asset['visualSignature']=image_signature(avatar_file(self.directory,record)[0])
   except (OSError,ValueError):pass
  return record
 def _duplicate(self,record,others=None):
  skip_self=others is None
  others=others if others is not None else self.state['ready']+self.state['reserve']+self.state['history']+self.state.get('portraitPending',[])+self.state.get('portraitDeferred',[])
  for old in others:
   if skip_self and old['source']==record['source'] and old['id']==record['id']:continue
   reason=duplicate_reason(record,old)
   if reason:return old,reason
  return None,None
 def dedupe_inventory(self):
  with self.lock:
   existing=[self._visual(r) for r in self.state['history']]
   candidates=[(group,self._visual(r)) for group in ('ready','reserve','portraitPending','portraitDeferred') for r in self.state.get(group,[])]
   def quality(pair):
    group,r=pair;stat=r.get('sourceMetrics') or {}
    return (group in ('ready','reserve'),r.get('userPreference')=='more',sum(bool(r.get(k)) for k in FIELDS),stat.get('messages') or 0,stat.get('views') or 0,stat.get('downloads') or 0)
   keep={group:[] for group in ('ready','reserve','portraitPending','portraitDeferred')}
   for group,r in sorted(candidates,key=quality,reverse=True):
    old,reason=self._duplicate(r,existing)
    if old:self.state['retired'].append({**r,'retiredReason':'去重：'+reason,'duplicateOf':old['id'],'retiredAt':now_iso()})
    else:keep[group].append(r);existing.append(r)
   self.state.update(keep);self._promote();self._save()
 def apply_preferences(self):
  with self.lock:
   pending=self.state.setdefault('portraitPending',[]);existing={r['source'] for r in pending}
   for group in ('ready','reserve'):
    keep=[]
    for record in self.state[group]:
     decision,reason,selected=self.taste.evaluate(record)
     if decision=='accept':keep.append(normalize_record(selected))
     elif decision=='pending':
      if record['source'] not in existing:pending.append(selected);existing.add(record['source'])
     else:self.state['retired'].append({**selected,'retiredReason':reason,'retiredAt':now_iso()})
    self.state[group]=keep
   for group in ('portraitPending','portraitDeferred'):
    keep=[]
    for record in self.state.get(group,[]):
     decision,reason,selected=self.taste.evaluate(record)
     if decision=='reject':self.state['retired'].append({**selected,'retiredReason':reason,'retiredAt':now_iso()})
     else:keep.append(normalize_record(selected))
    self.state[group]=keep
   self._promote();self._save();self.wake.set()
 def feedback(self,record_id,choice):
  if not self.taste:raise ValueError('偏好服务未启用')
  with self.lock:
   old_queries=self.taste.model()['queries'][:]
   record=self.get_record(record_id,include_retired=True);self.taste.feedback(record,choice)
   if choice in ('more','clear'):
    from preference_learning import identities
    keys=set(identities(record));keep=[]
    for r in self.state['retired']:
     if r.get('retiredReason','').startswith('已按你的标注排除') and keys.intersection(identities(r)):
      candidate=copy.deepcopy(r);candidate.pop('retiredReason',None);candidate.pop('retiredAt',None)
      accepted,_,candidate=evaluate(candidate)
      if accepted and not self._duplicate(candidate)[0]:self.state.setdefault('portraitPending',[]).append(candidate)
      else:keep.append(r)
     else:keep.append(r)
    self.state['retired']=keep
   self.apply_preferences()
   if old_queries!=self.taste.model()['queries']:self.state['lastHeadCheck']=0;self._save();self.wake.set()
   if choice=='less':
    queue=self.state.get('portraitPending',[])
    for r in queue:
     if r['id']==record_id:self.state['retired'].append({**r,'retiredReason':'已按你的标注排除','retiredAt':now_iso()})
    self.state['portraitPending']=[r for r in queue if r['id']!=record_id];self._save()
   learned=self.taste.snapshot()['learning']
   return {'ok':True,'choice':choice,'learning':learned,'effect':'已撤销本条及镜像的偏好；符合规则的角色回到候选，不增加使用次数。' if choice=='clear' else '已提高本条及相似题材、画风的优先级；重复点击不重复计票。' if choice=='more' else '已排除本条和同图／相同人设；相似特征累计独立反例后降权，不封禁整个题材。'}
 def review_portrait(self,record_id,digest,style,medium=None,evidence=None):
  if not self.taste:raise ValueError('偏好服务未启用')
  with self.lock:
   pending_group=next((group for group in ('portraitPending','portraitDeferred') if any(r['id']==record_id for r in self.state.get(group,[]))),None)
   record=next((copy.deepcopy(r) for r in self.state.get(pending_group,[]) if r['id']==record_id),None)
   if record is None:raise ValueError('该角色已处理，请刷新')
   asset=record.get('avatarAsset') or {}
   if not digest or digest!=asset.get('sha256'):raise ValueError('头像已更新，请刷新')
   if evidence is not None:
    if not isinstance(evidence,dict) or evidence.get('sha256')!=digest:raise ValueError('自动头像检查证据与图片不匹配')
    asset['autoReview']=copy.deepcopy(evidence)
   self.taste.review(record,style,medium);asset['review']='approved';asset['reviewedAt']=now_iso()
   decision,reason,selected=self.taste.evaluate(record)
   queue=self.state.setdefault(pending_group,[]);self.state[pending_group]=[r for r in queue if r['id']!=record_id]
   if decision=='accept':
    from pool_avatars import display_avatar
    selected=normalize_record(display_avatar(selected));selected['addedAt']=now_iso()
    old,duplicate=self._duplicate(selected)
    if old:
     self.state['retired'].append({**selected,'retiredReason':'去重：'+duplicate,'duplicateOf':old['id'],'retiredAt':now_iso()});self._save();return {'ok':True,'decision':'duplicate'}
    if len(self.state['ready'])+len(self.state['reserve'])<self.target+self.reserve_target:(self.state['ready'] if len(self.state['ready'])<self.target else self.state['reserve']).append(selected)
    else:self.state['portraitPending'].append(selected)
   elif decision=='reject':self.state['retired'].append({**selected,'retiredReason':reason,'retiredAt':now_iso()})
   else:self.state['portraitPending'].append(selected)
   self._save();self.wake.set();return {'ok':True,'decision':decision}
 def defer_portraits(self,record_ids,reason='图像判断不清，保留待确认'):
  """Quarantine uncertain artwork without blocking new collection or voting it down."""
  with self.lock:
   selected=set(record_ids);keep=[];deferred=self.state.setdefault('portraitDeferred',[]);known={r['id'] for r in deferred};moved=0
   for record in self.state.get('portraitPending',[]):
    if record['id'] in selected and (record.get('avatarAsset') or {}).get('review')!='approved':
     if record['id'] not in known:
      record['pendingReason']=str(reason)[:240];record['deferredAt']=now_iso();deferred.append(record);known.add(record['id']);moved+=1
    else:keep.append(record)
   self.state['portraitPending']=keep
   while len(deferred)>200:
    old=deferred.pop(0);self.state['retired'].append({**old,'retiredReason':'候选归档：疑难头像保留记录','retiredAt':now_iso()})
   if moved:self._save()
   return moved
 def _promote(self):
  while len(self.state['ready'])<self.target and self.state['reserve']:
   replacement=self._replacement()
   if replacement is None:break
   self.state['ready'].append(replacement)
 def _replacement(self):
  reserve=self.state['reserve']
  if not self.taste:return reserve.pop(0) if reserve else None
  candidates=[]
  for group in ('reserve','portraitPending'):
   for i,r in enumerate(self.state[group]):
    decision,_,selected=self.taste.evaluate(r)
    if decision=='accept':candidates.append((group,i,selected))
  preferred=self._mix_candidates([entry[2] for entry in candidates],self.state['ready'],self.target)
  candidates=[entry for entry in candidates if entry[2] in preferred]
  if not candidates:return None
  from selection_rules import rank_records
  ranked=rank_records([entry[2] for entry in candidates])
  if not ranked:return None
  best=ranked[0]
  group,index,selected=next(entry for entry in candidates if entry[2]['id']==best['id'])
  self.state[group].pop(index);selected['addedAt']=now_iso();return normalize_record(selected)
 def _mix_candidates(self,candidates,stock,total):
  percent=getattr(self.client,'target_nsfw_percent',80)
  if sum(r.get('sourceRating')=='NSFW' for r in stock)<(total*percent+50)//100:
   matching=[r for r in candidates if r.get('sourceRating')=='NSFW']
   if matching:candidates=matching
  if self.taste and self.taste.state.get('targetRealisticPercent',0):
   want=sum(self.taste.is_realistic(r) for r in stock)<self.taste.realistic_target(total)
   matching=[r for r in candidates if self.taste.is_realistic(r)==want]
   if matching:candidates=matching
  return candidates
 def snapshot(self,include_history=False,include_pending=False):
  with self.lock:
   out={'revision':self.state['revision'],'profile':PROFILE_LABEL,'target':self.target,'reserveTarget':self.reserve_target,'readyCount':len(self.state['ready']),'reserveCount':len(self.state['reserve']),'usedCount':len(self.state['history']),'ready':self.state['ready'],'history':self.state['history'] if include_history else [],'busy':self.busy,'status':self.state['status'],'lastAddedAt':self.state['lastAddedAt'],'sourceErrors':self.source_failures}
   all_pending=self.state.get('portraitPending',[])+self.state.get('portraitDeferred',[])
   out.update(portraitPendingCount=len(all_pending),portraitDeferredCount=len(self.state.get('portraitDeferred',[])),portraitPending=all_pending if include_pending else [],preferences=self.taste.snapshot() if self.taste else None)
   if self.taste:out['visualMix']={'realisticCount':sum(self.taste.is_realistic(r) for r in self.state['ready']),'targetCount':self.taste.realistic_target(self.target),'targetPercent':self.taste.state.get('targetRealisticPercent',0),'realisticQueued':sum(self.taste.is_realistic(r) and (r.get('avatarAsset') or {}).get('review')=='approved' for r in self.state.get('portraitPending',[]))}
   percent=getattr(self.client,'target_nsfw_percent',80);count=sum(r.get('sourceRating')=='NSFW' for r in self.state['ready'])
   out['sourceMix']={'nsfwCount':count,'sfwCount':sum(r.get('sourceRating')=='SFW' for r in self.state['ready']),'targetPercent':percent,'targetCount':(self.target*percent+50)//100,'nsfwPending':sum(r.get('sourceRating')=='NSFW' for r in all_pending),'targetMet':count>=(self.target*percent+50)//100}
   out['lastFeedback']=None
   if self.taste:
    from preference_learning import vote_order
    votes=self.taste.state['feedback'];ordered=sorted(votes.items(),key=lambda pair:vote_order(pair[1]));latest=ordered[-1] if ordered else None
    if latest:
     record=next((r for g in ('ready','reserve','history','portraitPending','portraitDeferred','retired') for r in self.state.get(g,[]) if r['source']==latest[0]),None)
     if record:out['lastFeedback']={'id':record['id'],'name':record['name'],'choice':latest[1]['choice']}
   if not include_history:out.pop('history',None)
   if not include_pending:out.pop('portraitPending',None)
   return copy.deepcopy(out)
 def get_record(self,record_id,include_retired=False):
  with self.lock:
   for group in ('ready','reserve','history','portraitPending','portraitDeferred')+ (('retired',) if include_retired else ()):
    for record in self.state.get(group,[]):
     if record['id']==record_id:return copy.deepcopy(record)
  raise KeyError('未找到该 bot')
 def consume(self,record_id):
  with self.lock:
   if any(r['id']==record_id for r in self.state['history']):return {'alreadyUsed':True,'usedId':record_id,'replacementId':None}
   index=next((i for i,r in enumerate(self.state['ready']) if r['id']==record_id),None)
   if index is None:raise KeyError('该 bot 不在待用库中，请刷新')
   record=self.state['ready'].pop(index);record['usedAt']=now_iso();self.state['history'].append(record)
   replacement=self._replacement()
   if replacement:self.state['ready'].insert(index,replacement)
   self.state['status']='正在补足备用库存' if replacement else '正在补足待用库存';self._save();self.wake.set()
   return {'alreadyUsed':False,'usedId':record_id,'replacementId':replacement['id'] if replacement else None}
 def edit(self,record_id,fields):
  with self.lock:
   for group in ('ready','history'):
    for index,record in enumerate(self.state[group]):
     if record['id']!=record_id:continue
     draft=copy.deepcopy(record);draft['fieldSources']=dict(record.get('fieldSources') or {})
     for key in FIELDS:
      if key not in fields:continue
      if not isinstance(fields[key],str):raise ValueError('字段必须是文本')
      if key=='avatar' and fields[key].strip()!=record.get(key):raise ValueError('更换头像需重新读取来源并核验，不能直接修改已核验头像地址')
      if fields[key].strip()!=record.get(key):draft[key]=fields[key].strip();draft['fieldSources'][key]='手动编辑'
     accepted,reason,draft=evaluate(draft)
     if not accepted:raise ValueError('未保存：'+reason)
     if self.taste:
      decision,reason,draft=self.taste.evaluate(draft)
      if decision!='accept':raise ValueError('未保存：'+reason)
     old,reason=self._duplicate(draft)
     if old:raise ValueError('未保存：与已有角色重复（'+reason+'）')
     self.state[group][index]=normalize_record(draft);self._save();return copy.deepcopy(self.state[group][index])
   raise KeyError('未找到该 bot')
 def refresh(self):
  with self.lock:self.state['lastHeadCheck']=0;self.state['status']='正在检查新 bot 并补足库存';self._save()
  self.wake.set()
 def review_avatar(self,record_id,digest,approved):
  if not isinstance(approved,bool):raise ValueError('Invalid avatar review')
  with self.lock:
   for group in ('ready','reserve','history'):
    for i,r in enumerate(self.state[group]):
     if r['id']!=record_id:continue
     asset=r.get('avatarAsset') or {}
     if not digest or asset.get('sha256')!=digest or asset.get('source')!=r.get('avatarSource'):raise ValueError('头像已更新，请刷新后再确认')
     if approved:asset.update(review='approved',reviewedAt=now_iso())
     asset['displayHidden']=not approved
     accepted,_,selected=evaluate(r)
     if not accepted:raise ValueError('角色不符合当前选题规则')
     self.state[group][i]=normalize_record(selected);out=copy.deepcopy(self.state[group][i]);self._save()
     if self.taste:self.apply_preferences()
     return out
   raise KeyError('未找到该 bot')
 def _failure(self,key,error):
  with self.lock:
   self.state['failures'][key]={'error':str(error)[:500],'retryAfter':time.time()+1800}
   if len(self.state['failures'])>1000:self.state['failures'].pop(next(iter(self.state['failures'])))
   self._save()
 def accept(self,result):
  record=normalize_record(result['record']);key=record['source'];fp=fingerprint(record)
  with self.lock:
   if key in self.seen_keys or fp in self.seen_content:return False
  if self.taste:
   accepted,_,record=evaluate(record)
   if not accepted:
    with self.lock:self.seen_keys.add(key);self._save()
    return False
   decision,_,record=self.taste.evaluate(record)
   if decision=='reject':
    with self.lock:self.seen_keys.add(key);self._save()
    return False
   record['id']=hashlib.sha256(key.encode()).hexdigest()[:24]
   if not record.get('avatarAsset'):
    data=result.get('image')
    if data is None:data,url=fetch_record_avatar(record);record['avatarSource']=url
    record['avatarAsset']=cache_avatar(self.directory,record,data)
   with self.lock:
    old,duplicate=self._duplicate(record)
    if old:
     self.seen_keys.add(key);self.state['retired'].append({**record,'retiredReason':'去重：'+duplicate,'duplicateOf':old['id'],'retiredAt':now_iso()});self._save();return False
   decision,reason,record=self.taste.evaluate(record)
   if decision!='accept':
    with self.lock:
     if key not in self.seen_keys:
      if decision=='pending' and len(self.state.get('portraitPending',[]))>=self.target+self.reserve_target:return False
      self.seen_keys.add(key);self.seen_content.add(fp)
      if decision=='pending':record['pendingReason']=reason;self.state.setdefault('portraitPending',[]).append(record)
      else:self.state['retired'].append({**record,'retiredReason':reason,'retiredAt':now_iso()})
      self._save()
    return False
  with self.lock:
   if key in self.seen_keys or fp in self.seen_content:return False
   accepted,reason,record=evaluate(record)
   if not accepted:self.seen_keys.add(key);self._save();return False
   record=normalize_record(record)
   # A shelf slot must contain real character content, never a failed request or empty placeholder.
   if record['error'] or any(not record[k] for k in ('name','avatar','prompt','greet')):
    self.seen_keys.add(key);self._save();return False
   if len(self.state['ready'])+len(self.state['reserve'])>=self.target+self.reserve_target:
    if len(self.state.get('portraitPending',[]))<self.target+self.reserve_target:
     record['id']=hashlib.sha256(key.encode()).hexdigest()[:24];record['pendingReason']='库存已满，已读取的后续候选'
     self.state.setdefault('portraitPending',[]).append(record);self.seen_keys.add(key);self.seen_content.add(fp);self._save()
    return False
   record['id']=hashlib.sha256(key.encode()).hexdigest()[:24];record['addedAt']=now_iso()
   if result.get('image') and not record.get('avatarAsset'):
    record['avatarAsset']=cache_avatar(self.directory,record,result['image'])
   old,duplicate=self._duplicate(record)
   if old:self.seen_keys.add(key);self._save();return False
   self.seen_keys.add(key);self.seen_content.add(fp);self.state['failures'].pop(key,None)
   (self.state['ready'] if len(self.state['ready'])<self.target else self.state['reserve']).append(record)
   self.state['lastAddedAt']=now_iso();self._save();return True
 def _available(self,item):
  key=item['url'];failure=self.state['failures'].get(key)
  return key not in self.seen_keys and key not in self.pending and not(failure and failure['retryAfter']>time.time())
 def sync_avatars(self):
  with self.lock:
   records=[copy.deepcopy(r) for r in self.state['ready']+self.state['reserve'] if not r.get('avatarAsset') and r.get('avatarRetryAfter',0)<time.time()]
  def download(record):
   try:
    data,url=fetch_record_avatar(record);candidate={**record,'avatarSource':url}
    return record,cache_avatar(self.directory,candidate,data),None
   except Exception as error:return record,None,str(error)[:200]
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
   for original,asset,error in executor.map(download,records):
    with self.lock:
     for record in self.state['ready']+self.state['reserve']+self.state['history']:
      if record['id']==original['id'] and record.get('avatarSource')==original.get('avatarSource'):
       if asset:record['avatarAsset']=asset;record['avatarSource']=asset['source'];record.pop('avatarError',None)
       else:record['avatarError']=error;record['avatarRetryAfter']=time.time()+300
       self._save();break
 def _next_candidate(self):
  with self.lock:
   offset=self.state['sourceTurn']%len(self.sources);self.state['sourceTurn']+=1
   sources=list(self.sources[offset:]+self.sources[:offset])
   preferred=[s for s in getattr(self.client,'nsfw_sources',()) if s in sources]
   want=sum(r.get('sourceRating')=='NSFW' for r in self.state['ready'])<(self.target*getattr(self.client,'target_nsfw_percent',80)+50)//100
   turn=self.state.get('discoverySourceTurn',0);self.state['discoverySourceTurn']=turn+1
  if want and preferred and turn%4!=3:sources=preferred+[s for s in sources if s not in preferred]
  for source in sources:
   with self.lock:
    if self.source_failures.get(source,{}).get('retryAfter',0)>time.time():continue
    queue=self.state['queues'].setdefault(source,[])
    while queue:
     item=queue.pop(0)
     if self._available(item):self.pending.add(item['url']);return item
    page=self.state['pages'].get(source,1) or 1
   try:
    result=self._catalogue(source,page,20)
    with self.lock:
     self.state['queues'][source].extend(result['items']);self.state['pages'][source]=result['nextPage'] or 1;self.source_failures.pop(source,None);self._save()
     queue=self.state['queues'][source]
     while queue:
      item=queue.pop(0)
      if self._available(item):self.pending.add(item['url']);return item
   except Exception as error:
    with self.lock:self.source_failures[source]={'error':str(error)[:300],'retryAfter':time.time()+60}
  return None
 def _catalogue(self,source,page,limit=20):
  supports=getattr(self.client,'supports_query_override',False)
  if callable(supports):supports=supports(source)
  with self.lock:
   turn=self.state.get('preferenceQueryTurn',0);self.state['preferenceQueryTurn']=turn+1
   query=self.taste.discovery_query(turn) if self.taste and supports else None
   pages=self.state.setdefault('preferenceQueryPages',{});key=source+'|'+str(query);query_page=pages.get(key,1)
  if not query:result=self.client.catalogue(source,page,limit)
  else:
   result=self.client.catalogue(source,query_page,limit,query=query)
   with self.lock:pages[key]=result.get('nextPage') or 1
   result={**result,'nextPage':page,'preferenceQuery':query}
  result['items']=sorted(result['items'],key=lambda item:item.get('sourceRating')=='NSFW',reverse=True)
  return result
 def refill_batch(self):
  self.last_batch_attempted=0
  promoted=0
  with self.lock:
   if self.taste:
    keep=[]
    from selection_rules import rank_records
    queue=rank_records([self.taste.evaluate(r)[2] for r in self.state.get('portraitPending',[])])
    for _ in range(len(queue)):
     index=0
     eligible=[r for r in queue if self.taste.evaluate(r)[0]=='accept']
     preferred=self._mix_candidates(eligible,self.state['ready']+self.state['reserve'],self.target+self.reserve_target)
     if preferred:index=queue.index(preferred[0])
     r=queue.pop(index)
     decision,_,selected=self.taste.evaluate(r)
     if decision=='accept' and len(self.state['ready'])+len(self.state['reserve'])<self.target+self.reserve_target:
      selected['addedAt']=now_iso();(self.state['ready'] if len(self.state['ready'])<self.target else self.state['reserve']).append(normalize_record(selected));promoted+=1
     else:keep.append(r)
    self.state['portraitPending']=keep
    if promoted:self.state['lastAddedAt']=now_iso();self._save()
   needed=self.target+self.reserve_target-len(self.state['ready'])-len(self.state['reserve'])
   if self.taste and len(self.state.get('portraitPending',[]))>=self.target+self.reserve_target:self.state['status']='新头像待核验；待用库存仅计符合偏好的人像';self._save();return 0
  with self.lock:buffer_space=self.target+self.reserve_target-len(self.state.get('portraitPending',[]))
  if needed<=0 and buffer_space<=0:return promoted
  candidates=[]
  for _ in range(min(3,needed if needed>0 else buffer_space)):
   item=self._next_candidate()
   if item:candidates.append(item)
  if not candidates:return 0
  self.last_batch_attempted=len(candidates)
  added=0
  with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
   tasks={pool.submit(self.client.read,item):item for item in candidates}
   for task in concurrent.futures.as_completed(tasks):
    item=tasks[task]
    try:added+=int(self.accept(task.result()))
    except Exception as error:self._failure(item['url'],error)
    finally:
     with self.lock:self.pending.discard(item['url'])
  return added
 def auto_review_pending(self,limit=3):
  reviewer=getattr(self,'portrait_reviewer',None)
  if reviewer is None or not reviewer.available():return 0
  version=reviewer.snapshot()['modelVersion'];reviewed=0
  with self.lock:
   records=[copy.deepcopy(r) for r in self.state.get('portraitPending',[]) if (r.get('avatarAsset') or {}).get('review')!='approved' and r.get('autoReviewAttempt')!={'version':version,'sha256':(r.get('avatarAsset') or {}).get('sha256')}][:limit]
  for record in records:
   if self.stop_event.is_set():break
   result=reviewer(record);digest=(record.get('avatarAsset') or {}).get('sha256')
   if result.get('decision') in ('accept','reject') and result.get('style'):
    try:self.review_portrait(record['id'],digest,result['style'],result.get('medium'),result.get('evidence'));reviewed+=1
    except (ValueError,KeyError):pass
   else:
    with self.lock:
     for current in self.state.get('portraitPending',[]):
      if current['id']==record['id'] and (current.get('avatarAsset') or {}).get('sha256')==digest:
       current['autoReviewAttempt']={'version':version,'sha256':digest};current['pendingReason']=result.get('reason','图像判断不清');self._save();break
    self.defer_portraits([record['id']],result.get('reason','图像判断不清'))
  return reviewed
 def refresh_definitions(self,limit=2,interval=21600):
  """Bounded full-source refresh; manual edits survive and new pictures need review."""
  with self.lock:
   records=sorted((copy.deepcopy(r) for r in self.state['ready']+self.state['reserve'] if time.time()-r.get('sourceCheckedAt',0)>=interval),key=lambda r:r.get('sourceCheckedAt',0))[:limit]
  for original in records:
   if self.stop_event.is_set() or self.collection_paused.is_set():break
   try:
    source=original.get('catalogueSource') or original.get('metricsSource');endpoint=(original.get('raw') or {}).get('apiEndpoint')
    if not endpoint:raise ValueError('来源未提供完整详情更新地址')
    result=self.client.read({'source':source,'url':original['source'],'endpoint':endpoint})
    fresh=result['record'];draft=copy.deepcopy(original)
    for key in FIELDS:
     if original.get('fieldSources',{}).get(key)!='手动编辑':
      draft[key]=fresh.get(key,'');draft.setdefault('fieldSources',{})[key]=fresh.get('fieldSources',{}).get(key,'原站更新')
    for key in ('raw','sourceMetrics','sourceRating','metricsAt','sourceRanking','readAt'):
     if key in fresh:draft[key]=fresh[key]
    draft['avatarSource']=fresh.get('avatarSource') or fresh.get('avatar','')
    image=result.get('image')
    if image is None:image,url=fetch_record_avatar(draft);draft['avatarSource']=url
    draft['avatarAsset']=cache_avatar(self.directory,draft,image)
    draft['sourceCheckedAt']=time.time();draft['sourceUpdatedAt']=now_iso();draft.pop('sourceUpdateError',None)
    accepted,reason,draft=evaluate(draft)
    decision='accept' if accepted else 'reject'
    if accepted and self.taste:decision,reason,draft=self.taste.evaluate(draft)
    with self.lock:
     for group in ('ready','reserve'):
      index=next((i for i,r in enumerate(self.state[group]) if r['id']==original['id']),None)
      if index is None:continue
      current=self.state[group][index]
      if any(current.get(k)!=original.get(k) for k in (*FIELDS,'fieldSources','avatarAsset','userPreference')):break
      old,duplicate=self._duplicate(draft)
      if old:decision='reject';reason='去重：'+duplicate
      if decision=='accept':self.state[group][index]=normalize_record(draft)
      else:
       self.state[group].pop(index)
       if decision=='pending':draft['pendingReason']=reason;self.state.setdefault('portraitPending',[]).append(normalize_record(draft))
       else:self.state['retired'].append({**draft,'retiredReason':reason,'retiredAt':now_iso()})
      self.seen_content.add(fingerprint(draft));self._promote();self._save();break
   except Exception as error:
    with self.lock:
     for r in self.state['ready']+self.state['reserve']:
      if r['id']==original['id']:r['sourceUpdateError']=str(error)[:180];r['sourceCheckedAt']=time.time();self._save();break
 def check_heads(self):
  for source in self.sources:
   if self.stop_event.is_set() or self.collection_paused.is_set():break
   try:
    result=self._catalogue(source,1,20)
    with self.lock:
     known={v['url'] for v in self.state['queues'][source]};fresh=[v for v in result['items'] if v['url'] not in known and self._available(v)]
     self.state['queues'][source]=fresh+self.state['queues'][source];self.source_failures.pop(source,None)
   except Exception as error:
    with self.lock:self.source_failures[source]={'error':str(error)[:300],'retryAfter':time.time()+60}
  if self.collection_paused.is_set():return
  if hasattr(self.client,'read_metadata'):self.refresh_metrics()
  with self.lock:self.state['lastHeadCheck']=time.time();self._save()
 def refresh_metrics(self):
  from datetime import datetime
  def stale(r):
   try:return time.time()-datetime.fromisoformat(r.get('metricsAt','')).timestamp()>=900
   except (ValueError,TypeError):return True
  with self.lock:records=[copy.deepcopy(r) for r in self.state['ready']+self.state['reserve'] if stale(r)]
  def read(record):
   if self.collection_paused.is_set():return record['id'],None,None
   try:return record['id'],self.client.read_metadata(record),None
   except Exception as error:return record['id'],None,str(error)[:180]
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
   for role_id,value,error in executor.map(read,records):
    if value is None and error is None:continue
    with self.lock:
     for r in self.state['ready']+self.state['reserve']+self.state['history']:
      if r['id']==role_id:
       if value:r.update(value);r.pop('metricsError',None)
       else:r['metricsError']=error
       self._save();break
  if self.taste:self.apply_preferences()
 def _run(self):
  self.stop_event.wait(3)
  while not self.stop_event.is_set():
   self.wake.clear()
   if self.collection_paused.is_set():self.wake.wait(1);continue
   try:
    if time.time()-self.state['lastHeadCheck']>=900:
     with self.collection_lock:
      if not self.collection_paused.is_set():self.check_heads();self.refresh_definitions()
    with self.lock:
     needs=len(self.state['ready'])<self.target or len(self.state['reserve'])<self.reserve_target;self.busy=needs
    if needs:
     with self.lock:self.state['status']='正在补货；已读取 '+str(len(self.state['ready']))+' / '+str(self.target)
     with self.collection_lock:
      if self.collection_paused.is_set():added=0
      else:self.auto_review_pending();added=self.refill_batch();self.auto_review_pending()
     with self.lock:
      full=len(self.state['ready'])==self.target and len(self.state['reserve'])==self.reserve_target
      self.busy=not full
      if full:self.state['status']='库存已满，用一个自动补一个';self._save()
     self.wake.wait(0.5 if added or self.last_batch_attempted else 8)
    else:
     with self.collection_lock:
      if not self.collection_paused.is_set():self.sync_avatars();self.auto_review_pending()
     if time.time()-self.state.get('lastCandidateCheck',0)>=60:
      with self.collection_lock:
       if not self.collection_paused.is_set():self.refill_batch()
      with self.lock:self.state['lastCandidateCheck']=time.time();self._save()
     with self.lock:self.busy=False;self.state['status']='库存已满，用一个自动补一个'
     self.wake.wait(30)
   except Exception as error:
    with self.lock:self.busy=False;self.state['status']='补货暂时失败：'+str(error)[:200]
    self.wake.wait(30)
 def start(self):
  if self.thread and self.thread.is_alive():return
  self.thread=threading.Thread(target=self._run,name='bot-shelf-refill',daemon=True);self.thread.start()
 def close(self):
  self.stop_event.set();self.wake.set()
  if self.thread and self.thread is not threading.current_thread():self.thread.join()
  self.writer_lock.close()
