import json,tempfile,threading,time,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from api_server import Handler
from automation_jobs import AutomationJobs
from portrait_review import LocalPortraitReviewer
from pool_store import PoolStore
from test_pool_store import FakeCatalogue

class AutomationApiTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.pool=PoolStore(self.temp.name,FakeCatalogue(),target=1,reserve_target=1)
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.pool=self.pool
  self.pool.client.sources=self.pool.sources;self.server.reviewer=LocalPortraitReviewer(self.temp.name)
  self.server.automation=AutomationJobs(self.temp.name,self.pool)
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):
  self.server.shutdown();self.server.server_close();self.thread.join();self.server.automation.close();self.server.reviewer.close();self.pool.close();self.temp.cleanup()
 def request(self,path,body=None,origin=None):
  headers={'Content-Type':'application/json'}
  if origin:headers['Origin']=origin
  request=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
  with urllib.request.urlopen(request,timeout=5) as response:return json.load(response)
 def test_start_without_urls_runs_real_ingest_and_progress_survives_polling(self):
  started=self.request('/api/automation/start',{'maxRead':2});job_id=started['job']['id']
  for _ in range(100):
   status=self.request('/api/automation')
   if not status['job']['active']:break
   time.sleep(.01)
  self.assertEqual(status['job']['id'],job_id);self.assertEqual(status['job']['read'],2);self.assertEqual(status['job']['accepted'],2)
  self.assertEqual(self.pool.snapshot()['usedCount'],0);self.assertEqual(len(status['sources']),3)
 def test_cross_origin_start_cannot_launch_a_job(self):
  with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/automation/start',{'maxRead':2},'https://unrelated.example')
  self.assertEqual(error.exception.code,403);self.assertEqual(self.server.automation.snapshot()['job']['status'],'idle')
 def test_results_include_only_this_jobs_approved_records_and_do_not_consume(self):
  self.request('/api/automation/start',{'maxRead':2})
  for _ in range(100):
   if not self.server.automation.snapshot()['job']['active']:break
   time.sleep(.01)
  record=self.pool.state['ready'][0];record['avatarAsset']={'review':'approved'}
  results=self.request('/api/automation/results');self.assertEqual(results['count'],1);self.assertEqual(results['records'][0]['id'],record['id']);self.assertEqual(self.pool.snapshot()['usedCount'],0)
 def test_invalid_budget_and_source_selection_leave_state_unchanged(self):
  for body in ({'maxRead':0},{'maxRead':True},{'maxRead':501},{'sources':['unknown']}):
   with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/automation/start',body)
   self.assertEqual(error.exception.code,400)
  self.assertEqual(self.server.automation.snapshot()['job']['status'],'idle')
 def test_excluded_types_are_hidden_from_batch_results_even_for_used_history(self):
  from taste_profile import TasteProfile
  from test_preference_learning import role
  self.pool.taste=TasteProfile(self.temp.name)
  used,excluded=role('used'),role('excluded')
  for record in (used,excluded):self.pool.taste.review(record,'detailed_illustration')
  used['usedAt']='2026-10-22T00:00:00Z';self.pool.state['history']=[used]
  self.server.automation.state['qualifiedIds']=[used['id']]
  self.assertEqual(self.request('/api/automation/results')['count'],1)
  self.pool.taste.feedback(excluded,'less')
  self.assertEqual(self.request('/api/automation/results')['count'],0)
  self.assertEqual(self.pool.snapshot()['usedCount'],1);self.assertEqual(self.pool.state['history'][0]['usedAt'],used['usedAt'])

if __name__=='__main__':unittest.main()
