"""Test the real shelf through HTTP on an isolated copy, preserving user history."""
import json,shutil,tempfile,threading,urllib.request,sys,time,collections
from pathlib import Path
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from api_server import Handler,fetch_public
from pool_store import PoolStore,fingerprint
from pool_sources import PublicCatalogue
from pool_policy import evaluate

class AuditedCatalogue(PublicCatalogue):
 def __init__(self,fetcher):super().__init__(fetcher);self.rejections=collections.Counter()
 def read(self,item):
  result=super().read(item);ok,reason,_=evaluate(result['record'])
  if not ok:self.rejections[reason]+=1
  return result

source=json.loads((ROOT/'data'/'pool.json').read_text(encoding='utf-8'))
assert len(source['ready'])==50 and len(source['reserve'])==5
report={'readyBefore':50,'reserveBefore':5,'uniqueSources':len({r['source'] for r in source['ready']}),'uniqueContents':len({fingerprint(r) for r in source['ready']})}
with tempfile.TemporaryDirectory(prefix='role-reader-pool-test-') as directory:
 Path(directory,'pool.json').write_text(json.dumps(source,ensure_ascii=False),encoding='utf-8')
 client=AuditedCatalogue(fetch_public);store=PoolStore(directory,client);server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.pool=store
 worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start();base='http://127.0.0.1:'+str(server.server_port)
 def request(path,body=None):
  raw=json.dumps(body).encode() if body is not None else None
  req=urllib.request.Request(base+path,data=raw,headers={'Content-Type':'application/json'} if raw else {},method='POST' if raw else 'GET')
  with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
 try:
  before=request('/api/pool');old=before['ready'][0]['id'];first=request('/api/pool/use',{'id':old});second=request('/api/pool/use',{'id':old})
  assert first['pool']['readyCount']==50 and first['pool']['reserveCount']==4
  assert first['replacementId'] and first['replacementId']!=old
  assert second['alreadyUsed'] and second['pool']['usedCount']==1
  after=request('/api/pool?history=1');assert after['history'][0]['id']==old and old not in {r['id'] for r in after['ready']}
  used=after['usedCount'];request('/api/pool');assert request('/api/pool')['usedCount']==used
  refill_started=time.monotonic()
  for attempt in range(60):
   store.refill_batch()
   if store.snapshot()['reserveCount']==5:break
   if attempt%5==4:print(json.dumps({'refillBatches':attempt+1,'reserve':store.snapshot()['reserveCount'],'rejectionReasons':dict(client.rejections)},ensure_ascii=False),flush=True)
   if time.monotonic()-refill_started>120:break
  store.close();store=PoolStore(directory,PublicCatalogue(fetch_public));server.pool=store;restored=store.snapshot(True)
  assert restored['readyCount']==50 and restored['reserveCount']==5 and restored['usedCount']==1, {'ready':restored['readyCount'],'reserve':restored['reserveCount'],'sourceErrors':restored['sourceErrors'],'rejections':dict(client.rejections)}
  report.update(httpUseReplacedOne=True,duplicateClickIdempotent=True,exportsDoNotConsume=True,readyAfterUse=50,reserveAfterUse=4,reserveAfterRefill=5,historyPreservedAfterRestart=True,refillBatches=attempt+1,refillSeconds=round(time.monotonic()-refill_started,1),rejectionReasons=dict(client.rejections))
 finally:server.shutdown();server.server_close();store.close()
assert len(json.loads((ROOT/'data'/'pool.json').read_text(encoding='utf-8'))['history'])==len(source['history'])
(ROOT/'.test-output'/'pool-live-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
