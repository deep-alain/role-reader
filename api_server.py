"""Local public-character API client. Standard-library only; no credentials."""
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
import argparse,json,urllib.request,urllib.error,urllib.parse,re,webbrowser,base64
from pool_avatars import avatar_file
from source_metrics import CAPABILITIES
from discovery_sources import PublicDiscovery
from selection_rules import RuleStore,rank_records

ROOT=Path(__file__).resolve().parent
ALLOWED={
 'api.chub.ai':re.compile(r'^/api/characters/[^/]+/[^/]+$'),
 'gateway.chub.ai':re.compile(r'^/search$'),
 'charavault.net':re.compile(r'^/api/cards(?:/download/[^/]+/[^/]+)?$'),
 'botbooru.com':re.compile(r'^/(?:posts/|post/\d{1,12}|download/png/\d{1,12})$'),
}
QUERY_KEYS={'search','namespace','first','page','sort','asc','nsfw','nsfl','nsfw_only','include_forks','count','full','q','qtext','limit','offset','time_window'}
def validate_url(url):
 u=urllib.parse.urlsplit(url)
 if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or u.fragment:raise ValueError('Only configured public HTTPS endpoints are allowed')
 if u.hostname not in ALLOWED or not ALLOWED[u.hostname].fullmatch(u.path):raise ValueError('Endpoint is not in the public API allowlist')
 for part in u.path.split('/'):
  decoded=urllib.parse.unquote(part)
  if decoded in ('.','..') or '/' in decoded or '\\' in decoded or any(ord(c)<32 for c in decoded):raise ValueError('Invalid endpoint path')
 params=urllib.parse.parse_qs(u.query,keep_blank_values=True)
 if set(params)-QUERY_KEYS:raise ValueError('Unsupported query parameter')
 if 'time_window' in params and (u.hostname!='botbooru.com' or len(params['time_window'])!=1 or params['time_window'][0] not in ('day','week','month','all')):raise ValueError('Invalid time window')
 if u.hostname=='gateway.chub.ai' and (params.get('namespace',['characters'])!=['characters'] or params.get('nsfw',['false']) not in (['false'],['true']) or params.get('nsfl',['false'])!=['false']):raise ValueError('Only the public character catalogue without NSFL is supported')
 return url

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):return None

def fetch_public(url):
 validate_url(url)
 opener=urllib.request.build_opener(NoRedirect())
 request=urllib.request.Request(url,headers={'User-Agent':'RoleReader/1.1','Accept':'application/json, image/png;q=0.9, */*;q=0.8'})
 with opener.open(request,timeout=25) as response:
  limit=30*1024*1024
  if int(response.headers.get('Content-Length','0'))>limit:raise ValueError('API response exceeds 30 MB')
  data=response.read(limit+1)
  if len(data)>limit:raise ValueError('API response exceeds 30 MB')
  content_type=response.headers.get('Content-Type','application/octet-stream')
  if not any(t in content_type.lower() for t in ['application/json','image/png','application/octet-stream']):raise ValueError('API did not return JSON or a character-card image')
  return data,content_type

class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT/'extension'),**kwargs)
 def automation_snapshot(self):
  jobs=self.server.automation;registry=jobs.registry
  sources=[{'id':source,'mode':'full','fullDefinition':True} for source in registry.sources]
  sources.extend({'id':source,'mode':'metadata','fullDefinition':False} for source in jobs.discovery_sources)
  return {**jobs.snapshot(),'reviewer':self.server.reviewer.snapshot(),'sources':sources}
 def pool_snapshot(self,include_history=False,include_pending=False):
  snapshot=self.server.pool.snapshot(include_history,include_pending)
  if include_pending:snapshot['portraitPending']=rank_records(snapshot['portraitPending'])
  if hasattr(self.server,'rules'):
   snapshot['ready']=rank_records(snapshot['ready'],self.server.rules.snapshot()['rules'])
   snapshot['matchingCount']=len(snapshot['ready'])
  return snapshot
 def json_reply(self,value,status=200):
  data=json.dumps(value,ensure_ascii=False).encode('utf-8');self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
 def do_GET(self):
  host=self.headers.get('Host','').split(':')[0]
  if host not in ('127.0.0.1','localhost'):return self.json_reply({'error':'Localhost access only'},403)
  parsed=urllib.parse.urlsplit(self.path)
  if parsed.path=='/api/status':return self.json_reply({'roleReaderApi':True,'version':'1.12.1','sources':list(ALLOWED),'pool':hasattr(self.server,'pool'),'feedbackTypeExclusions':True,'coverFeedback':True})
  if parsed.path=='/api/automation':return self.json_reply(self.automation_snapshot()) if hasattr(self.server,'automation') else self.json_reply({'error':'自动读取服务未启动'},503)
  if parsed.path=='/api/automation/results':
   if not hasattr(self.server,'automation'):return self.json_reply({'error':'自动读取服务未启动'},503)
   with self.server.automation.lock:
    job_id=self.server.automation.state.get('id');ids=self.server.automation.state.get('qualifiedIds',[])[:]
   records=[]
   for role_id in ids:
    try:record=self.server.pool.get_record(role_id)
    except KeyError:continue
    if (record.get('avatarAsset') or {}).get('review')=='approved':
     taste=getattr(self.server.pool,'taste',None)
     if taste:
      decision,_,record=taste.evaluate(record)
      if decision!='accept':continue
     records.append(record)
   records=rank_records(records,self.server.rules.snapshot()['rules'] if hasattr(self.server,'rules') else None)
   return self.json_reply({'jobId':job_id,'records':records,'count':len(records)})
  if parsed.path=='/api/rules':return self.json_reply(self.server.rules.snapshot()) if hasattr(self.server,'rules') else self.json_reply({'error':'规则服务未启动'},503)
  if parsed.path=='/api/pool':
   if not hasattr(self.server,'pool'):return self.json_reply({'error':'库存服务未启动'},503)
   query=urllib.parse.parse_qs(parsed.query);snapshot=self.pool_snapshot(query.get('history')==['1'],query.get('pending')==['1'])
   if query.get('since')==[str(snapshot['revision'])]:snapshot.pop('ready',None);snapshot.pop('history',None);snapshot.pop('portraitPending',None)
   return self.json_reply(snapshot)
  if parsed.path=='/api/sources':return self.json_reply({'sources':CAPABILITIES,'enabledSources':list(getattr(getattr(self.server,'pool',None),'sources',[])),'selection':'成年故事型角色；原站分级单独展示','nsfwVerifiedSources':['chub','crushon','spicychat'],'priority':'人物偏好优先，同档按来源真实热度排序','adapterConfiguration':'config/sources.json'})
  if parsed.path=='/api/discovery':
   if self.headers.get('Origin') not in (None,f'http://{self.headers.get("Host")}') or self.headers.get('Sec-Fetch-Site')=='cross-site':return self.json_reply({'error':'Local same-origin requests only'},403)
   try:
    query=urllib.parse.parse_qs(parsed.query,keep_blank_values=True)
    if set(query)-{'source','q','sort','nsfw','page'} or any(len(v)!=1 for v in query.values()):raise ValueError('无效的候选查询参数')
    nsfw=query.get('nsfw',['1'])[0]
    if nsfw not in ('0','1'):raise ValueError('NSFW 筛选参数无效')
    if not hasattr(self.server,'discovery'):self.server.discovery=PublicDiscovery()
    return self.json_reply(self.server.discovery.search(query.get('source',['spicychat'])[0],query.get('q',['roommate'])[0],query.get('sort',['popular'])[0],nsfw=='1',int(query.get('page',['1'])[0])))
   except (ValueError,KeyError,urllib.error.URLError,TimeoutError) as error:return self.json_reply({'error':str(error)},502)
  if parsed.path=='/api/pool/avatar':
   if not hasattr(self.server,'pool'):return self.json_reply({'error':'库存服务未启动'},503)
   record_id=urllib.parse.parse_qs(parsed.query).get('id',[''])[0]
   if not re.fullmatch(r'[a-f0-9]{24}',record_id):return self.json_reply({'error':'Invalid record ID'},400)
   try:
    record=self.server.pool.get_record(record_id)
    if record.get('avatarVisibility')=='covered' and urllib.parse.parse_qs(parsed.query).get('preview')!=['1']:data=base64.b64decode(record['avatar'].split(',',1)[1]);content_type='image/svg+xml'
    else:data,content_type=avatar_file(self.server.pool.directory,record)
    self.send_response(200);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-cache');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
   except (ValueError,KeyError,OSError,urllib.error.URLError,TimeoutError):self.json_reply({'error':'头像暂时不可用'},404)
   except (ConnectionError,BrokenPipeError):pass
   return
  if parsed.path=='/api/fetch':
   if self.headers.get('Origin') not in (None,f'http://{self.headers.get("Host")}'):return self.json_reply({'error':'Cross-origin API access denied'},403)
   target=urllib.parse.parse_qs(parsed.query).get('url',[''])[0]
   try:
    data,content_type=fetch_public(target);self.send_response(200);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
   except urllib.error.HTTPError as e:self.json_reply({'error':f'Upstream HTTP {e.code}; no authentication or access checks bypassed'},e.code if 400<=e.code<600 else 502)
   except (ValueError,urllib.error.URLError,TimeoutError) as e:self.json_reply({'error':str(e)},502)
   except (ConnectionError,BrokenPipeError):pass
   return
  if parsed.path=='/':self.path='/app.html'
  super().do_GET()
 def do_POST(self):
  if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost') or self.headers.get('Origin') not in (None,f'http://{self.headers.get("Host")}') or self.headers.get('Sec-Fetch-Site')=='cross-site':return self.json_reply({'error':'Local same-origin requests only'},403)
  if not hasattr(self.server,'pool'):return self.json_reply({'error':'库存服务未启动'},503)
  try:
   length=int(self.headers.get('Content-Length','0'))
   if not 0<length<=2*1024*1024:return self.json_reply({'error':'Invalid request size'},413)
   if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.json_reply({'error':'Expected JSON'},415)
   body=json.loads(self.rfile.read(length));path=urllib.parse.urlsplit(self.path).path
   if not isinstance(body,dict):raise ValueError('Expected JSON object')
   if path in ('/api/automation/start','/api/automation/cancel'):
    if not hasattr(self.server,'automation'):return self.json_reply({'error':'自动读取服务未启动'},503)
    if path.endswith('/start'):self.server.automation.start(body.get('maxRead',120),body.get('sources'))
    else:self.server.automation.cancel()
    return self.json_reply(self.automation_snapshot())
   if path=='/api/rules':
    result=self.server.rules.save(body);self.server.pool.touch_view();return self.json_reply({**result,'pool':self.pool_snapshot()})
   if path=='/api/pool/use':return self.json_reply({**self.server.pool.consume(body.get('id')),'pool':self.pool_snapshot()})
   if path=='/api/pool/feedback':return self.json_reply({**self.server.pool.feedback(body.get('id'),body.get('choice')),'pool':self.pool_snapshot(include_pending=True)})
   if path=='/api/pool/cover-feedback':return self.json_reply({**self.server.pool.cover_feedback(body.get('id'),body.get('choice')),'pool':self.pool_snapshot(include_pending=True)})
   if path=='/api/pool/portrait-review':return self.json_reply({**self.server.pool.review_portrait(body.get('id'),body.get('sha256'),body.get('style'),body.get('medium')),'pool':self.pool_snapshot(include_pending=True)})
   if path=='/api/pool/refresh':self.server.pool.refresh();return self.json_reply({'ok':True,'pool':self.pool_snapshot()})
   if path=='/api/pool/avatar-review':return self.json_reply({'record':self.server.pool.review_avatar(body.get('id'),body.get('sha256'),body.get('approved'))})
   if path=='/api/pool/edit':
    if not isinstance(body.get('fields'),dict):raise ValueError('Invalid fields')
    return self.json_reply({'record':self.server.pool.edit(body.get('id'),body['fields'])})
   return self.json_reply({'error':'Unknown endpoint'},404)
  except (ValueError,TypeError) as error:return self.json_reply({'error':str(error)},400)
  except KeyError as error:return self.json_reply({'error':str(error)},409)
 def log_message(self,fmt,*args):
  # Do not log role names, search terms, or endpoint query strings.
  pass

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8766);parser.add_argument('--no-browser',action='store_true');parser.add_argument('--data-dir',type=Path,default=ROOT/'data');parser.add_argument('--no-pool',action='store_true');args=parser.parse_args()
 try:server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
 except OSError as e:
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/api/status',timeout=2) as existing:
    status=json.load(existing)
   if status.get('roleReaderApi') and status.get('version')=='1.12.1':
    url=f'http://127.0.0.1:{args.port}/app.html';print('Role Reader is already running: '+url,flush=True)
    if not args.no_browser:webbrowser.open(url)
    raise SystemExit(0)
  except (OSError,ValueError):pass
  raise SystemExit(f'Unable to start local reader on port {args.port}: {e}')
 if not args.no_pool:
  from source_registry import SourceRegistry
  from pool_store import PoolStore
  from taste_profile import TasteProfile
  server.pool=PoolStore(args.data_dir,SourceRegistry(fetch_public,ROOT/'config/sources.json'),taste=lambda:TasteProfile(args.data_dir,ROOT/'taste_examples.json'));server.pool.start()
  from automation_jobs import AutomationJobs
  from portrait_review import LocalPortraitReviewer
  server.reviewer=LocalPortraitReviewer(args.data_dir)
  if server.reviewer._files_ready():server.reviewer.prepare()
  server.pool.portrait_reviewer=server.reviewer
  server.discovery=PublicDiscovery()
  server.automation=AutomationJobs(args.data_dir,server.pool,portrait_reviewer=server.reviewer,discovery=lambda source:server.discovery.search(source,'roommate' if source=='spicychat' else 'gothic',nsfw_only=True))
 else:
  from directory_lock import DirectoryLock
  server.data_lock=DirectoryLock(args.data_dir)
 server.rules=RuleStore(args.data_dir)
 url=f'http://127.0.0.1:{args.port}/app.html';print('Role Reader API client: '+url,flush=True)
 if not args.no_browser:webbrowser.open(url)
 try:server.serve_forever()
 except KeyboardInterrupt:pass
 finally:
  server.server_close()
  if hasattr(server,'automation'):server.automation.close()
  if hasattr(server,'reviewer'):server.reviewer.close()
  if hasattr(server,'pool'):server.pool.close()
  if hasattr(server,'data_lock'):server.data_lock.close()
