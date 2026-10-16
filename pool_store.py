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
  self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True);self.path=self.directory/'pool.json';self.client=client
  self.target=target;self.reserve_target=reserve_target;self.sources=tuple(getattr(client,"sources",SOURCES));self.taste=taste;self.lock=threading.RLock();self.wake=threading.Event();self.stop_event=threading.Event();self.thread=None;self.busy=False;self.pending=set();self.source_failures={};self.last_batch_attempted=0
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
  if self.state.get('cataloguePolicy')!='popularity-v1':
   self.state['cataloguePolicy']='popularity-v1';self.state['pages']={s:1 for s in self.sources};self.state['queues']={s:[] for s in self.sources};self.state['lastHeadCheck']=0
  for source in self.sources:self.state['pages'].setdefault(source,1);self.state['queues'].setdefault(source,[])
  if self.taste:self.apply_preferences()
  self.dedupe_inventory()
  with self.lock:self._promote();self._save()
 def _save(self):
  self.state['revision']+=1
  self.state['seenKeys']=sorted(self.seen_keys);self.state['seenContent']=sorted(self.seen_content)
  tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.state,ensure_ascii=False,separators=(',',':')),encoding='utf-8');os.replace(tmp,self.path)
 def touch_view(self):
  with self.lock:self._save()
 def _visual(self,record):
  asset=record.get('avatarAsset') or {}
  if asset and not asset.get('visualSignature'):
   from pool_avatars import avatar_file
   from duplicate_guard import image_signature
   try:asset['visualSignature']=image_signature(avatar_file(self.directory,record)[0])
   except (OSError,ValueError):pass
  return record
 def _duplicate(self,record,others=None):
  skip_self=others is None
  others=others if others is not None else self.state['ready']+self.state['reserve']+self.state['history']+self.state.get('portraitPending',[])
  for old in others:
   if skip_self and old['source']==record['source'] and old['id']==record['id']:continue
   reason=duplicate_reason(record,old)
   if reason:return old,reason
  return None,None
 def dedupe_inventory(self):
  with self.lock:
   existing=[self._visual(r) for r in self.state['history']]
   candidates=[(group,self._visual(r)) for group in ('ready','reserve','portraitPending') for r in self.state.get(group,[])]
   def quality(pair):
    group,r=pair;stat=r.get('sourceMetrics') or {}
    return (group!='portraitPending',r.get('userPreference')=='more',sum(bool(r.get(k)) for k in FIELDS),stat.get('messages') or 0,stat.get('views') or 0,stat.get('downloads') or 0)
   keep={group:[] for group in ('ready','reserve','portraitPending')}
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
   self._promote();self._save();self.wake.set()
 def feedback(self,record_id,choice):
  if not self.taste:raise ValueError('偏好服务未启用')
  with self.lock:
   record=self.get_record(record_id);self.taste.feedback(record,choice);self.apply_preferences()
   if choice=='less':
    queue=self.state.get('portraitPending',[])
    for r in queue:
     if r['id']==record_id:self.state['retired'].append({**r,'retiredReason':'已按你的标注排除','retiredAt':now_iso()})
    self.state['portraitPending']=[r for r in queue if r['id']!=record_id];self._save()
   return {'ok':True,'choice':choice}
 def review_portrait(self,record_id,digest,style):
  if not self.taste:raise ValueError('偏好服务未启用')
  with self.lock:
   record=next((copy.deepcopy(r) for r in self.state.get('portraitPending',[]) if r['id']==record_id),None)
   if record is None:raise ValueError('该角色已处理，请刷新')
   asset=record.get('avatarAsset') or {}
   if not digest or digest!=asset.get('sha256'):raise ValueError('头像已更新，请刷新')
   self.taste.review(record,style);asset['review']='approved';asset['reviewedAt']=now_iso()
   decision,reason,selected=self.taste.evaluate(record)
   queue=self.state.setdefault('portraitPending',[]);self.state['portraitPending']=[r for r in queue if r['id']!=record_id]
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
 def _promote(self):
  while len(self.state['ready'])<self.target and self.state['reserve']:self.state['ready'].append(self.state['reserve'].pop(0))
 def snapshot(self,include_history=False,include_pending=False):
  with self.lock:
   out={'revision':self.state['revision'],'profile':PROFILE_LABEL,'target':self.target,'reserveTarget':self.reserve_target,'readyCount':len(self.state['ready']),'reserveCount':len(self.state['reserve']),'usedCount':len(self.state['history']),'ready':self.state['ready'],'history':self.state['history'] if include_history else [],'busy':self.busy,'status':self.state['status'],'lastAddedAt':self.state['lastAddedAt'],'sourceErrors':self.source_failures}
   out.update(portraitPendingCount=len(self.state.get('portraitPending',[])),portraitPending=self.state.get('portraitPending',[]) if include_pending else [],preferences=self.taste.snapshot() if self.taste else None)
   return copy.deepcopy(out)
 def get_record(self,record_id):
  with self.lock:
   for group in ('ready','reserve','history','portraitPending'):
    for record in self.state.get(group,[]):
     if record['id']==record_id:return copy.deepcopy(record)
  raise KeyError('未找到该 bot')
 def consume(self,record_id):
  with self.lock:
   if any(r['id']==record_id for r in self.state['history']):return {'alreadyUsed':True,'usedId':record_id,'replacementId':None}
   index=next((i for i,r in enumerate(self.state['ready']) if r['id']==record_id),None)
   if index is None:raise KeyError('该 bot 不在待用库中，请刷新')
   record=self.state['ready'].pop(index);record['usedAt']=now_iso();self.state['history'].append(record)
   replacement=self.state['reserve'].pop(0) if self.state['reserve'] else None
   if replacement:self.state['ready'].insert(index,replacement)
   self.state['status']='正在补足备用库存' if replacement else '正在补足待用库存';self._save();self.wake.set()
   return {'alreadyUsed':False,'usedId':record_id,'replacementId':replacement['id'] if replacement else None}
 def edit(self,record_id,fields):
  with self.lock:
   for group in ('ready','history'):
    for index,record in enumerate(self.state[group]):
     if record['id']!=record_id:continue
     record['fieldSources']=dict(record.get('fieldSources') or {})
     for key in FIELDS:
      if key in fields and isinstance(fields[key],str) and fields[key]!=record.get(key):record[key]=fields[key].strip();record['fieldSources'][key]='手动编辑'
     self.state[group][index]=normalize_record(record);self._save();return copy.deepcopy(self.state[group][index])
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
     asset.update(review='approved' if approved else 'hidden',reviewedAt=now_iso())
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
   if len(self.state['ready'])+len(self.state['reserve'])>=self.target+self.reserve_target:return False
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
  for _ in self.sources:
   with self.lock:
    source=self.sources[self.state['sourceTurn']%len(self.sources)];self.state['sourceTurn']+=1
    if self.source_failures.get(source,{}).get('retryAfter',0)>time.time():continue
    queue=self.state['queues'].setdefault(source,[])
    while queue:
     item=queue.pop(0)
     if self._available(item):self.pending.add(item['url']);return item
    page=self.state['pages'].get(source,1) or 1
   try:
    result=self.client.catalogue(source,page,20)
    with self.lock:
     self.state['queues'][source].extend(result['items']);self.state['pages'][source]=result['nextPage'] or 1;self.source_failures.pop(source,None);self._save()
     queue=self.state['queues'][source]
     while queue:
      item=queue.pop(0)
      if self._available(item):self.pending.add(item['url']);return item
   except Exception as error:
    with self.lock:self.source_failures[source]={'error':str(error)[:300],'retryAfter':time.time()+60}
  return None
 def refill_batch(self):
  self.last_batch_attempted=0
  promoted=0
  with self.lock:
   if self.taste:
    keep=[]
    for r in self.state.get('portraitPending',[]):
     decision,_,selected=self.taste.evaluate(r)
     if decision=='accept' and len(self.state['ready'])+len(self.state['reserve'])<self.target+self.reserve_target:
      selected['addedAt']=now_iso();(self.state['ready'] if len(self.state['ready'])<self.target else self.state['reserve']).append(normalize_record(selected));promoted+=1
     else:keep.append(r)
    self.state['portraitPending']=keep
    if promoted:self._save()
   needed=self.target+self.reserve_target-len(self.state['ready'])-len(self.state['reserve'])
   if self.taste and len(self.state.get('portraitPending',[]))>=self.target+self.reserve_target:self.state['status']='新头像待核验；待用库存仅计符合偏好的人像';self._save();return 0
  if needed<=0:return promoted
  candidates=[]
  for _ in range(min(3,needed)):
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
 def check_heads(self):
  for source in self.sources:
   if self.stop_event.is_set():break
   try:
    result=self.client.catalogue(source,1,20)
    with self.lock:
     known={v['url'] for v in self.state['queues'][source]};fresh=[v for v in result['items'] if v['url'] not in known and self._available(v)]
     self.state['queues'][source]=fresh+self.state['queues'][source];self.source_failures.pop(source,None)
   except Exception as error:
    with self.lock:self.source_failures[source]={'error':str(error)[:300],'retryAfter':time.time()+60}
  if hasattr(self.client,'read_metadata'):self.refresh_metrics()
  with self.lock:self.state['lastHeadCheck']=time.time();self._save()
 def refresh_metrics(self):
  from datetime import datetime
  def stale(r):
   try:return time.time()-datetime.fromisoformat(r.get('metricsAt','')).timestamp()>=900
   except (ValueError,TypeError):return True
  with self.lock:records=[copy.deepcopy(r) for r in self.state['ready']+self.state['reserve'] if stale(r)]
  def read(record):
   try:return record['id'],self.client.read_metadata(record),None
   except Exception as error:return record['id'],None,str(error)[:180]
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
   for role_id,value,error in executor.map(read,records):
    with self.lock:
     for r in self.state['ready']+self.state['reserve']+self.state['history']:
      if r['id']==role_id:
       if value:r.update(value);r.pop('metricsError',None)
       else:r['metricsError']=error
       self._save();break
  if self.taste:self.apply_preferences()
 def _run(self):
  while not self.stop_event.is_set():
   self.wake.clear()
   try:
    with self.lock:
     needs=len(self.state['ready'])<self.target or len(self.state['reserve'])<self.reserve_target;self.busy=needs
    if needs:
     with self.lock:self.state['status']='正在补货；已读取 '+str(len(self.state['ready']))+' / '+str(self.target)
     added=self.refill_batch()
     with self.lock:
      full=len(self.state['ready'])==self.target and len(self.state['reserve'])==self.reserve_target
      self.busy=not full
      if full:self.state['status']='库存已满，用一个自动补一个';self._save()
     self.wake.wait(0.5 if added or self.last_batch_attempted else 8)
    else:
     self.sync_avatars()
     if time.time()-self.state['lastHeadCheck']>=900:self.check_heads()
     with self.lock:self.busy=False;self.state['status']='库存已满，用一个自动补一个'
     self.wake.wait(30)
   except Exception as error:
    with self.lock:self.busy=False;self.state['status']='补货暂时失败：'+str(error)[:200]
    self.wake.wait(30)
 def start(self):
  if self.thread and self.thread.is_alive():return
  self.thread=threading.Thread(target=self._run,name='bot-shelf-refill',daemon=True);self.thread.start()
 def close(self):self.stop_event.set();self.wake.set()
