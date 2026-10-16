"""Read-only verification of every current shelf avatar through its display API."""
import concurrent.futures,hashlib,json,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
BASE='http://127.0.0.1:8766'
def get(path):
 with urllib.request.urlopen(BASE+path,timeout=20) as response:return response.read(),response.headers.get_content_type()
before=json.loads(get('/api/pool?history=1')[0]);state=json.loads((ROOT/'data/pool.json').read_text(encoding='utf-8'))
records=state['ready']+state['reserve']
assert len(records)==55 and before['readyCount']==50 and before['reserveCount']==5
def verify(record):
 data,mime=get('/api/pool/avatar?id='+record['id']+'&v='+record['avatarAsset']['sha256'])
 assert record['avatarVisibility']=='source'
 assert mime==record['avatarAsset']['mime'] and hashlib.sha256(data).hexdigest()==record['avatarAsset']['sha256']
 assert mime in ('image/png','image/jpeg','image/webp') and not data.startswith(b'<svg')
 return {'id':record['id'],'mime':mime,'bytes':len(data),'sourceImageMatches':True}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:results=list(executor.map(verify,records))
after=json.loads(get('/api/pool?history=1')[0])
assert [r['id'] for r in before['ready']]==[r['id'] for r in after['ready']]
assert before['usedCount']==after['usedCount']
js=get('/app.js')[0].decode();css=get('/app.css')[0].decode();html=get('/app.html')[0].decode()
assert 'img.src=avatar' in js and 'avatar-preview' in js and '点击头像可查看大图' in html
assert '.person .record-avatar{width:64px;height:80px;' in css
report={'readyAvatars':50,'reserveAvatars':5,'sourceImagesServed':len(results),'placeholderResponses':0,'stockUnchanged':True,'usedCount':after['usedCount'],'checks':results}
(ROOT/'.test-output/avatar-live-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='checks'},ensure_ascii=False))
