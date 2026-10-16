import json,tempfile,threading,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from api_server import Handler
from selection_rules import RuleStore
from pool_store import PoolStore
from test_pool_store import FakeCatalogue

class RulesApiTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.client=FakeCatalogue();self.pool=PoolStore(self.temp.name,self.client,target=2,reserve_target=1)
  for index,views in enumerate((1,200,30)):
   result=self.client.read({'url':'https://example.org/'+str(index)});result['record'].update(metricsSource='example',sourceMetrics={'views':views});self.pool.accept(result)
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.pool=self.pool;self.server.rules=RuleStore(self.temp.name);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):
  self.server.shutdown();self.server.server_close();self.thread.join();self.pool.close();self.temp.cleanup()
 def request(self,path,body=None,origin=None):
  headers={'Content-Type':'application/json'}
  if origin:headers['Origin']=origin
  request=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
  with urllib.request.urlopen(request,timeout=5) as response:return json.load(response)
 def test_rules_filter_display_but_preserve_all_stock_and_history(self):
  before=self.pool.snapshot(True);result=self.request('/api/rules',{'minimumViews':100})
  self.assertEqual(result['pool']['matchingCount'],1);self.assertEqual(result['pool']['readyCount'],2);self.assertEqual(result['pool']['reserveCount'],1);self.assertEqual(result['pool']['usedCount'],0)
  after=self.pool.snapshot(True)
  for key in ('ready','history','reserveCount','usedCount'):self.assertEqual(before[key],after[key])
  current=self.request('/api/pool');self.assertEqual(current['ready'][0]['sourceMetrics']['views'],200)
  unchanged=self.request('/api/pool?since='+str(current['revision']));self.assertNotIn('ready',unchanged);self.assertEqual(unchanged['matchingCount'],1)
 def test_get_and_consume_responses_apply_the_same_ordering(self):
  before=self.request('/api/pool');self.assertEqual(before['ready'][0]['sourceMetrics']['views'],200)
  # Only this explicit action consumes stock, inside an isolated temporary shelf.
  after=self.request('/api/pool/use',{'id':before['ready'][0]['id']})['pool']
  self.assertEqual([r['sourceMetrics']['views'] for r in after['ready']],[30,1]);self.assertEqual(after['usedCount'],1)
 def test_cross_origin_rules_write_is_denied_without_mutation(self):
  before=self.server.rules.snapshot()
  with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/rules',{'minimumViews':999},origin='https://unrelated.example')
  self.assertEqual(error.exception.code,403);self.assertEqual(self.server.rules.snapshot(),before)

if __name__=='__main__':unittest.main()
