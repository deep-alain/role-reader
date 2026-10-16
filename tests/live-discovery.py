"""Read-only checks through the running page's API; never consumes stock."""
import concurrent.futures,json,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
BASE='http://127.0.0.1:8766'
def read(path):
 with urllib.request.urlopen(BASE+path,timeout=70) as response:return json.load(response)
before=read('/api/pool?history=1')
def check(source):
 value=read('/api/discovery?source='+source+'&q=roommate&sort=popular&nsfw=1&page=1')
 items=value['items'];assert items,'No verified candidates returned'
 assert all(r['sourceRating']=='NSFW' and r['definitionAccess'] and 'prompt' not in r for r in items)
 counts=[r['metrics']['messages'] for r in items if r['metrics'].get('messages') is not None]
 assert counts==sorted(counts,reverse=True)
 assert 'apiKey' not in json.dumps(value) and 'api_key' not in json.dumps(value)
 return {'source':source,'returned':len(items),'nsfwVerified':True,'messageCounts':counts,'completeDefinitionClaimed':False,'note':value['note']}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(check,['crushon','spicychat']))
after=read('/api/pool?history=1');assert [r['id'] for r in before['ready']]==[r['id'] for r in after['ready']];assert before['usedCount']==after['usedCount']
report={'discovery':results,'ready':after['readyCount'],'reserve':after['reserveCount'],'used':after['usedCount'],'stockUnchanged':True,'sources':read('/api/sources')}
(ROOT/'.test-output/discovery-live-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='sources'},ensure_ascii=False))
