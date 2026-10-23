"""Real-network smoke test from a distributable ZIP with an empty user profile.
--reuse-models reuses only pinned models and Python dependencies, never user data.
"""
import argparse,json,os,shutil,socket,subprocess,sys,time,urllib.request,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def request(base,path,body=None):
 data=json.dumps(body).encode() if body is not None else None
 req=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'} if data is not None else {})
 with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)

def link_or_copy(source,destination):
 try:os.link(source,destination)
 except OSError:shutil.copy2(source,destination)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('package',type=Path);parser.add_argument('--reuse-models',action='store_true');parser.add_argument('--timeout',type=int,default=240);parser.add_argument('--max-read',type=int,default=12);args=parser.parse_args()
 output=ROOT/'.test-output'/('fresh-user-'+time.strftime('%Y%m%d-%H%M%S'));output.mkdir(parents=True)
 with zipfile.ZipFile(args.package) as archive:
  assert not any('/data/' in n or 'taste_examples.json' in n for n in archive.namelist())
  archive.extractall(output)
 app=output/'角色资料读取器';data=app/'data';data.mkdir()
 if args.reuse_models:
  for folder in ('models','vision-runtime'):
   shutil.copytree(ROOT/'data'/folder,data/folder,copy_function=link_or_copy)
 assert not (data/'pool.json').exists() and not (data/'taste_profile.json').exists()
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base='http://127.0.0.1:'+str(port);report={'emptyUserProfile':True,'reusedModelsOnly':args.reuse_models,'manualVotes':0,'productionConsumptionRequests':0,'port':port}
 log=(output/'server.log').open('w',encoding='utf-8');process=subprocess.Popen([sys.executable,'api_server.py','--no-browser','--port',str(port)],cwd=app,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 try:
  deadline=time.monotonic()+args.timeout
  while True:
   if process.poll() is not None:raise RuntimeError('fresh server exited; see server.log')
   try:report['version']=request(base,'/api/status')['version'];break
   except OSError:
    if time.monotonic()>deadline:raise TimeoutError('startup timeout')
    time.sleep(1)
  job=request(base,'/api/automation/start',{'maxRead':args.max_read})['job'];print(json.dumps({'phase':'started','jobId':job['id'],'port':port}),flush=True)
  while job['active'] and time.monotonic()<deadline:
   time.sleep(3);job=request(base,'/api/automation')['job']
  if job['active']:
   request(base,'/api/automation/cancel',{});report['timedOut']=True
   for _ in range(12):
    time.sleep(1);job=request(base,'/api/automation')['job']
    if not job['active']:break
  shelf=request(base,'/api/pool?pending=1');profile=json.loads((data/'taste_profile.json').read_text(encoding='utf-8'))
  state=json.loads((data/'pool.json').read_text(encoding='utf-8'));records=[r for g in ('ready','reserve','portraitPending','portraitDeferred','retired') for r in state.get(g,[])]
  assert not profile['feedback'];assert shelf['usedCount']==0
  assert all('virtualloverai.com' not in r.get('source','') for r in records)
  report.update(job=job,readyCount=shelf['readyCount'],reserveCount=shelf['reserveCount'],candidateCount=shelf['portraitPendingCount'],usedCount=shelf['usedCount'],targetRealisticPercent=profile['targetRealisticPercent'],independentAutomaticReadSucceeded=job['readSucceeded']>0,independentAutomaticApprovalSucceeded=bool(shelf['readyCount']))
 except Exception as error:report['error']=str(error)
 finally:
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
  log.close();(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:v for k,v in report.items() if k!='job'},ensure_ascii=False),flush=True);print(str(output/'report.json'),flush=True)
if __name__=='__main__':main()
