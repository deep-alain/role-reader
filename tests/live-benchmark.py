"""Read real sources; test replacement HTTP only against disposable shelf copies."""
import collections,copy,hashlib,json,os,re,shutil,sys,threading,time,urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from api_server import Handler
from pool_store import PoolStore
from pool_sources import FIELDS
from pool_policy import evaluate
from taste_profile import TasteProfile,LIMITS
from source_metrics import number
from selection_rules import rank_records,RuleStore,adult_state

BASE='http://127.0.0.1:8766'
REFERENCE='https://www.virtualloverai.com/en?cid=24'

def request(path,body=None,base=BASE):
 data=json.dumps(body).encode() if body is not None else None
 req=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'} if data is not None else {})
 with urllib.request.urlopen(req,timeout=45) as response:return json.load(response)

def save(path,value):
 path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def load(name):return json.loads((ROOT/'data'/name).read_text(encoding='utf-8'))

def all_records(state):return [r for group in ('ready','reserve','history','portraitPending','portraitDeferred','retired') for r in state.get(group,[])]

def quality(record,taste):
 accepted,_,_=evaluate(record);decision,reason,selected=taste.evaluate(record)
 stat=record.get('sourceMetrics') or {};basis=next((k for k in LIMITS if number(stat.get(k)) is not None),None)
 checks={'adult':adult_state(record)=='verified','currentPolicy':accepted,'personalRules':decision=='accept',
  'approvedPortrait':(record.get('avatarAsset') or {}).get('review')=='approved',
  'nineFields':all(bool(record.get(k)) for k in FIELDS),
  'scenario60':len(record.get('scenario','').strip())>=60,'greeting120':len(record.get('greet','').strip())>=120,
  'definition600':len(record.get('prompt','').strip())>=600,
  'knownHeat':bool(basis and stat[basis]>=LIMITS[basis]),
  'externalCollectionSource':'virtualloverai.com' not in record.get('source','')}
 return {'id':record['id'],'name':record['name'],'source':record.get('source'),'sourceRating':record.get('sourceRating','unknown'),
  'completeFields':sum(bool(record.get(k)) for k in FIELDS),'lengths':{k:len(record.get(k,'').strip()) for k in ('intro','scenario','greet','prompt')},
  'checks':checks,'passed':all(checks.values()),'reason':reason,'coverScore':(selected.get('coverPreference') or {}).get('score')}

class OfflineCatalogue:
 sources=('chub','botbooru','charavault');target_nsfw_percent=80;nsfw_sources=('chub',)
 def catalogue(self,source,page=1,limit=20):return {'items':[],'nextPage':None}
 def read(self,item):raise RuntimeError('Replacement replay must not fetch or mutate the production shelf')

def replay(round_dir,state,strict=False,steps=10):
 folder=round_dir/('shelf-benchmark' if strict else 'shelf-current');folder.mkdir();(folder/'avatars').mkdir()
 for path in (ROOT/'data').glob('*.json'):
  shutil.copy2(path,folder/path.name)
 save(folder/'pool.json',state)
 assets=[r.get('avatarAsset') or {} for r in all_records(state)]
 for value in load('cover_preferences.json').get('references',{}).values():assets.append(value.get('avatarAsset') or {})
 for asset in assets:
  name=asset.get('file','')
  if not re.fullmatch(r'[a-f0-9]{24}-[a-f0-9]{16}\.(png|jpg|webp)',name):continue
  source=ROOT/'data/avatars'/name;target=folder/'avatars'/name
  if source.is_file() and not target.exists():
   try:os.link(source,target)
   except OSError:shutil.copy2(source,target)
 store=PoolStore(folder,OfflineCatalogue(),taste=lambda:TasteProfile(folder))
 if strict:
  for group in ('reserve','portraitPending'):
   store.state[group]=[record for record in store.state.get(group,[]) if quality(record,store.taste)['passed']]
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.pool=store;server.rules=RuleStore(folder)
 worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start();base='http://127.0.0.1:'+str(server.server_port)
 before=store.snapshot(True,True);events=[];history_before=copy.deepcopy(store.state['history'])
 shelf_quality=[quality(r,store.taste) for r in store.state['ready']]
 try:
  for step in range(steps):
   if not store.state['ready']:break
   old=rank_records(store.state['ready'])[0];started=time.monotonic()
   first=request('/api/pool/use',{'id':old['id']},base)
   second=request('/api/pool/use',{'id':old['id']},base)
   replacement=store.get_record(first['replacementId']) if first.get('replacementId') else None
   events.append({'step':step+1,'usedId':old['id'],'replacement':quality(replacement,store.taste) if replacement else None,
    'instantReplacement':replacement is not None,'readyCount':store.snapshot()['readyCount'],
    'idempotent':bool(second.get('alreadyUsed')) and second['pool']['usedCount']==first['pool']['usedCount'],
    'seconds':round(time.monotonic()-started,3)})
  after=store.snapshot(True,True)
 finally:server.shutdown();server.server_close();worker.join();store.close()
 reopened=PoolStore(folder,OfflineCatalogue(),taste=lambda:TasteProfile(folder))
 try:
  replacements=[e['replacement'] for e in events if e['replacement']]
  quality_counter=collections.Counter(k for r in shelf_quality for k,v in r['checks'].items() if not v)
  return {'before':{k:before[k] for k in ('readyCount','reserveCount','usedCount')},
   'after':{k:after[k] for k in ('readyCount','reserveCount','usedCount')},'steps':events,
   'instantReplacements':len(replacements),'replacementQualityPassed':sum(r['passed'] for r in replacements),
   'replacementNsfw':sum(r['sourceRating']=='NSFW' for r in replacements),
   'shelfQualityPassed':sum(r['passed'] for r in shelf_quality),'shelfQualityTotal':len(shelf_quality),
   'shelfQualityFailures':dict(quality_counter),'allDuplicateClicksIdempotent':all(e['idempotent'] for e in events),
   'historySurvivesRestart':len(reopened.state['history'])==len(history_before)+len(events) and reopened.state['history'][:len(history_before)]==history_before,
   'noUsedRoleReappeared':not({r['source'] for r in reopened.state['history']}&{r['source'] for r in reopened.state['ready']}),
   'uniqueShelfSources':len({r['source'] for r in reopened.state['ready']})==len(reopened.state['ready']),
   'allShelfQuality':shelf_quality}
 finally:reopened.close()


def main():
 STAMP=time.strftime('%Y%m%d-%H%M%S')
 OUT=ROOT/'.test-output'/('reference-rounds-'+STAMP)
 OUT.mkdir(parents=True)
 initial=request('/api/pool?history=1&pending=1')
 report={'referenceUrl':REFERENCE,'referenceIncludedInCollection':False,
  'referenceObservations':['ANIME category','adult age displayed','detailed character cover','short relationship hook','scenario and action/dialogue opening visible'],
  'evaluationLimits':['Full hidden prompt and chat quality on reference site are not observable','Character lengths below are this test\'s editorial checks, not requirements published by reference site','chatting on reference page is not comparable to source views/messages','instant replacement tests use a copy; no production /use requests'],
  'editorialChecks':{'nineFields':True,'minimumScenarioCharacters':60,'minimumGreetingCharacters':120,'minimumDefinitionCharacters':600,'minimumKnownHeat':dict(LIMITS)},
  'startedAt':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'productionUseRequests':0,'initialUsedCount':initial['usedCount'],'rounds':[]}
 save(OUT/'report.json',report)
 print(json.dumps({'output':str(OUT),'started':True,'roundsPlanned':3,'maxReadPerRound':80},ensure_ascii=False),flush=True)
 for index in range(1,4):
  round_dir=OUT/('round-'+str(index));round_dir.mkdir()
  before_state=load('pool.json');known={r['id'] for r in all_records(before_state)}
  active=request('/api/automation')
  if active['job'].get('active'):raise RuntimeError('Another task is active; not taking it over')
  job=request('/api/automation/start',{'maxRead':80})['job'];job_id=job['id'];started=time.monotonic();last_print=0
  while True:
   snapshot=request('/api/automation');current=snapshot['job']
   if current['id']!=job_id:raise RuntimeError('Job changed externally; preserving it')
   save(round_dir/'progress.json',snapshot)
   if time.monotonic()-last_print>=25 or not current['active']:
    print(json.dumps({'round':index,'job':current['status'],'read':current['read'],'full':current['readSucceeded'],'accepted':current['accepted'],'catalogues':current['catalogues'],'reasonCounts':current.get('reasonCounts',{})},ensure_ascii=False),flush=True);last_print=time.monotonic()
   if not current['active']:break
   if time.monotonic()-started>1200:
    request('/api/automation/cancel',{});raise TimeoutError('Collection round exceeded its 20 minute limit')
   time.sleep(6)
  state=load('pool.json');job_state=load('automation_jobs.json')
  assert all('virtualloverai.com' not in r.get('source','') for r in all_records(state)), 'Reference site entered collection unexpectedly'
  metrics=request('/api/pool');qualified=set(job_state.get('qualifiedIds',[]));new_qualified=qualified-known
  row={'round':index,'seconds':round(time.monotonic()-started,1),'job':snapshot['job'],
   'newQualifiedIds':sorted(new_qualified),'newQualifiedCount':len(new_qualified),
   'production':{k:metrics[k] for k in ('readyCount','reserveCount','usedCount','sourceMix','visualMix')},
    'replacementReplay':replay(round_dir,state),'benchmarkReplay':replay(round_dir,state,strict=True)}
  report['rounds'].append(row);save(round_dir/'result.json',row);save(OUT/'report.json',report)
  print(json.dumps({'round':index,'finished':True,'newQualified':len(new_qualified),'instantReplacements':row['replacementReplay']['instantReplacements'],'replacementQualityPassed':row['replacementReplay']['replacementQualityPassed'],'shelfQualityPassed':row['replacementReplay']['shelfQualityPassed'],'sourceMix':metrics['sourceMix']},ensure_ascii=False),flush=True)
 continuous=OUT/'continuous';continuous.mkdir()
 report['continuousReplay']=replay(continuous,load('pool.json'),steps=30)
 report['continuousBenchmarkReplay']=replay(continuous,load('pool.json'),strict=True,steps=30)
 final=request('/api/pool?history=1&pending=1');report['finalUsedCount']=final['usedCount'];report['finishedAt']=time.strftime('%Y-%m-%dT%H:%M:%S%z')
 report['totalFullReads']=sum(r['job']['readSucceeded'] for r in report['rounds'])
 report['totalActualRequests']=sum(r['job']['read'] for r in report['rounds'])
 report['newQualifiedUnique']=len(set(x for r in report['rounds'] for x in r['newQualifiedIds']))
 report['finalProduction']={k:final[k] for k in ('readyCount','reserveCount','usedCount','sourceMix','visualMix')}
 save(OUT/'report.json',report);save(ROOT/'.test-output/latest-reference-benchmark.json',{'directory':str(OUT),'report':str(OUT/'report.json')})
 print(json.dumps({'complete':True,'report':str(OUT/'report.json'),'fullReads':report['totalFullReads'],'newQualifiedUnique':report['newQualifiedUnique'],'productionUsed':report['finalUsedCount']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
