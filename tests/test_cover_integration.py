import copy,hashlib,io,json,tempfile,threading,unittest,urllib.request
from http.server import ThreadingHTTPServer
from PIL import Image
from api_server import Handler
from pool_avatars import cache_avatar
from pool_store import PoolStore
from portrait_review import CLIP_EMBEDDING_VERSION
from selection_rules import rank_records
from taste_profile import TasteProfile
from test_preference_learning import FakeCatalogue,role

def vector(a=1,b=0):return [a,b]+[0]*510

class CoverIntegrationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.taste=TasteProfile(self.temp.name)
  self.pool=PoolStore(self.temp.name,FakeCatalogue(),target=3,reserve_target=1,taste=self.taste)
  self.reference=self.record('reference','red');self.near=self.record('near','blue');self.far=self.record('far','green')
  self.pool.state['ready']=[self.reference,self.near,self.far]
  class Reviewer:
   def available(self):return True
   def embedding(_,record):return {'sha256':record['avatarAsset']['sha256'],'modelVersion':CLIP_EMBEDDING_VERSION,'embedding':vector()}
  self.pool.portrait_reviewer=Reviewer()
 def tearDown(self):self.pool.close();self.temp.cleanup()
 def record(self,key,color):
  record=role(key);record['id']=hashlib.sha256(key.encode()).hexdigest()[:24]
  data=io.BytesIO();Image.new('RGB',(24,24),color).save(data,format='PNG')
  record['avatarAsset']=cache_avatar(self.temp.name,record,data.getvalue());record['avatarAsset']['review']='approved'
  self.taste.review(record,'detailed_illustration');return record
 def test_cover_feedback_is_independent_of_story_votes_and_usage_and_can_be_undone(self):
  self.taste.feedback(self.reference,'more');before=copy.deepcopy(self.taste.state['feedback']);queries=self.taste.model()['queries'][:]
  self.pool.cover_feedback(self.reference['id'],'more')
  self.assertEqual(self.taste.state['feedback'],before);self.assertEqual(self.taste.model()['queries'],queries)
  self.assertEqual(self.pool.snapshot()['usedCount'],0);self.assertEqual(self.taste.covers.snapshot()['count'],1)
  self.pool.cover_feedback(self.reference['id'],'more');self.assertEqual(self.taste.covers.snapshot()['count'],1)
  self.pool.cover_feedback(self.reference['id'],'clear');self.assertEqual(self.taste.covers.snapshot()['count'],0)
  self.assertEqual(self.taste.state['feedback'],before);self.assertEqual(self.pool.snapshot()['usedCount'],0)
 def test_real_vector_similarity_changes_ranking_and_keeps_source_nsfw_first(self):
  self.pool.cover_feedback(self.reference['id'],'more')
  for record,value in ((self.near,vector(9,1)),(self.far,vector(1,9))):self.taste.covers.put_embedding(record,value,CLIP_EMBEDDING_VERSION)
  near=self.taste.evaluate(self.near)[2];far=self.taste.evaluate(self.far)[2]
  self.assertGreater(near['coverPreference']['score'],far['coverPreference']['score'])
  self.assertEqual(rank_records([far,near])[0]['name'],'near')
  far['sourceRating']='NSFW';near['sourceRating']='SFW';self.assertEqual(rank_records([near,far])[0]['name'],'far')
 def test_cover_reference_cannot_override_type_exclusions_or_portrait_checks(self):
  self.pool.cover_feedback(self.reference['id'],'more')
  self.taste.covers.put_embedding(self.near,vector(),CLIP_EMBEDDING_VERSION)
  self.taste.feedback(self.far,'less');self.assertEqual(self.taste.evaluate(self.near)[0],'reject')
  self.taste.feedback(self.far,'clear');pending=copy.deepcopy(self.near);pending['avatarAsset']['review']='pending'
  decision,_,selected=self.taste.evaluate(pending);self.assertEqual(decision,'pending');self.assertNotIn('coverPreference',selected)
 def test_background_index_populates_similarities_without_consuming_or_changing_story_votes(self):
  self.pool.cover_feedback(self.reference['id'],'more');before=copy.deepcopy(self.taste.state['feedback'])
  self.assertEqual(self.pool.index_cover_embeddings(limit=3),2)
  self.assertEqual(self.pool.index_cover_embeddings(limit=3),0)
  self.assertTrue(all('coverPreference' in r for r in self.pool.state['ready']))
  self.assertEqual(self.taste.state['feedback'],before);self.assertEqual(self.pool.snapshot()['usedCount'],0)
 def test_real_http_cover_endpoint_leaves_story_feedback_untouched(self):
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.pool=self.pool
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   request=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/pool/cover-feedback',data=json.dumps({'id':self.reference['id'],'choice':'more'}).encode(),headers={'Content-Type':'application/json'})
   with urllib.request.urlopen(request,timeout=5) as response:value=json.load(response)
   self.assertEqual(value['cover']['count'],1);self.assertTrue(value['pool']['preferences']['cover']['enabled'])
   self.assertEqual(self.taste.state['feedback'],{});self.assertEqual(value['pool']['usedCount'],0)
  finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
