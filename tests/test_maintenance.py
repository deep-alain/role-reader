import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pool_store import PoolStore
from taste_profile import TasteProfile
from pool_policy import evaluate
from pool_sources import PublicCatalogue
from test_pool_store import FakeCatalogue
from test_pool_avatars import PNG

class MaintenanceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.client=FakeCatalogue();self.pool=PoolStore(self.temp.name,self.client,target=1,reserve_target=1)
 def tearDown(self):self.pool.close();self.temp.cleanup()
 def add(self,key):
  result=self.client.read({'url':'https://example.org/'+key});self.pool.accept(result)
  return self.pool.get_record(result['record']['id'])
 def test_full_shelf_still_reads_a_bounded_candidate_buffer(self):
  for _ in range(3):self.pool.refill_batch()
  before=self.client.reads;self.pool.refill_batch();s=self.pool.snapshot(True,True)
  self.assertEqual(self.client.reads,before) # buffer is capped at target + reserve
  self.assertEqual((s['readyCount'],s['reserveCount'],s['portraitPendingCount'],s['usedCount']),(1,1,2,0))
  self.pool.state['portraitPending']=[];self.pool.refill_batch()
  self.assertGreater(self.client.reads,before);self.assertEqual(self.pool.snapshot()['usedCount'],0)
 def test_edit_rejects_invalid_and_duplicate_drafts_atomically(self):
  a=self.add('first');b=self.add('second');before=copy.deepcopy(self.pool.state)
  with self.assertRaises(ValueError):self.pool.edit(a['id'],{'age':'16','name':'changed'})
  self.assertEqual(self.pool.state,before)
  with self.assertRaises(ValueError):self.pool.edit(a['id'],{k:b[k] for k in ('name','prompt','greet')})
  self.assertEqual(self.pool.state,before)
 def test_data_directory_is_locked_even_in_another_process_and_released(self):
  script='from pool_store import PoolStore; from pathlib import Path; PoolStore(__import__("sys").argv[1],object())'
  result=subprocess.run([sys.executable,'-c',script,self.temp.name],capture_output=True)
  self.assertNotEqual(result.returncode,0);self.assertIn('ValueError',result.stderr.decode())
  self.pool.close();self.pool=PoolStore(self.temp.name,self.client,target=1,reserve_target=1)
  self.pool.touch_view()
 def test_omitted_views_cannot_clear_cached_history(self):
  self.add('first');self.pool.consume(self.pool.snapshot()['ready'][0]['id'])
  self.assertNotIn('history',self.pool.snapshot());self.assertEqual(len(self.pool.snapshot(True)['history']),1)
  self.assertNotIn('portraitPending',self.pool.snapshot())
 def test_full_definition_refresh_preserves_manual_fields_and_rechecks_same_url(self):
  r=self.add('first');self.pool.edit(r['id'],{'scenario':'My manual room.'})
  self.pool.state['ready'][0]['raw']['apiEndpoint']='endpoint';self.pool.state['ready'][0]['catalogueSource']='test'
  fresh=self.client.read({'url':r['source']});fresh['record']['scenario']='Author changed room.';fresh['record']['intro']='Updated author notes.'
  with patch.object(self.client,'read',return_value=fresh),patch('pool_store.fetch_record_avatar',return_value=(PNG,'https://example.org/a.png')) as fetch:
   self.pool.refresh_definitions();fetch.assert_called_once()
  saved=self.pool.get_record(r['id']);self.assertEqual(saved['scenario'],'My manual room.');self.assertEqual(saved['intro'],'Updated author notes.')
  self.assertTrue(saved['sourceCheckedAt']);self.assertEqual(saved['avatarAsset']['review'],'pending')
  self.assertEqual(self.pool.snapshot()['usedCount'],0)
 def test_inflight_source_refresh_cannot_overwrite_a_new_manual_scenario(self):
  r=self.add('first');self.pool.state['ready'][0]['raw']['apiEndpoint']='endpoint'
  fresh=self.client.read({'url':r['source']});fresh['image']=PNG
  def concurrent_read(item):
   self.pool.edit(r['id'],{'scenario':'Edited while reading source.'});return fresh
  with patch.object(self.client,'read',side_effect=concurrent_read):self.pool.refresh_definitions()
  self.assertEqual(self.pool.get_record(r['id'])['scenario'],'Edited while reading source.')
 def test_source_nsfw_mix_uses_real_ratings_and_excludes_unknown(self):
  self.add('first');s=self.pool.snapshot()['sourceMix'];self.assertEqual(s['nsfwCount'],0);self.assertFalse(s['targetMet'])
  candidates=[{'sourceRating':'unknown'},{'sourceRating':'NSFW'},{'sourceRating':'SFW'}]
  self.assertEqual(self.pool._mix_candidates(candidates,[],50),[candidates[1]])
 def test_public_catalogue_requests_nsfw_only_when_enabled(self):
  urls=[]
  def fetch(url):urls.append(url);return json.dumps({'data':{'nodes':[]}}).encode(),'application/json'
  PublicCatalogue(fetch,include_nsfw=True).catalogue('chub');self.assertIn('nsfw=true',urls[-1]);self.assertIn('nsfl=false',urls[-1])
  PublicCatalogue(fetch).catalogue('chub');self.assertIn('nsfw=false',urls[-1])

class FeedbackMaintenanceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.taste=TasteProfile(self.temp.name);self.pool=PoolStore(self.temp.name,FakeCatalogue(),target=1,reserve_target=1,taste=self.taste)
  result=self.pool.client.read({'url':'https://example.org/person'});result['image']=PNG;result['record']['sourceMetrics']={'messages':1000};self.pool.accept(result)
  self.role=self.pool.snapshot(include_pending=True)['portraitPending'][0];self.pool.review_portrait(self.role['id'],self.role['avatarAsset']['sha256'],'photo');self.role=self.pool.get_record(self.role['id'])
 def tearDown(self):self.pool.close();self.temp.cleanup()
 def test_hiding_approved_avatar_keeps_the_role_and_approval_after_restart(self):
  self.pool.review_avatar(self.role['id'],self.role['avatarAsset']['sha256'],False)
  self.assertEqual(self.pool.snapshot()['readyCount'],1);r=self.pool.get_record(self.role['id'])
  self.assertEqual(r['avatarVisibility'],'covered');self.assertEqual(r['avatarAsset']['review'],'approved')
  self.pool.close();self.pool=PoolStore(self.temp.name,FakeCatalogue(),target=1,reserve_target=1,taste=TasteProfile(self.temp.name))
  self.assertEqual(self.pool.get_record(self.role['id'])['avatarVisibility'],'covered');self.assertEqual(self.pool.snapshot()['readyCount'],1)
 def test_latest_mirror_vote_and_training_agree_and_clear_is_neutral(self):
  self.taste.feedback(self.role,'less');mirror={**self.role,'source':'https://other.example/mirror'}
  self.taste.feedback(mirror,'more');self.assertEqual(self.taste.evaluate(self.role)[0],'accept')
  self.assertEqual(self.taste.model()['uniqueExamples'],1)
  self.taste.feedback(self.role,'clear');self.assertEqual(self.taste.model()['uniqueExamples'],0);self.assertEqual(self.taste.evaluate(mirror)[0],'accept')
 def test_undo_can_restore_feedback_retired_role_without_marking_used(self):
  self.pool.feedback(self.role['id'],'less');self.assertEqual(self.pool.snapshot()['readyCount'],0)
  self.pool.feedback(self.role['id'],'clear');self.pool.refill_batch()
  self.assertEqual(self.pool.get_record(self.role['id'])['userPreference'],'');self.assertEqual(self.pool.snapshot()['usedCount'],0)
 def test_new_avatar_bytes_at_same_url_require_a_new_review(self):
  from io import BytesIO
  from PIL import Image
  stream=BytesIO();Image.new('RGB',(12,12),'blue').save(stream,format='PNG')
  self.pool.state['ready'][0]['raw']['apiEndpoint']='endpoint'
  updated=self.pool.client.read({'url':self.role['source']});updated['image']=stream.getvalue();updated['record']['sourceMetrics']={'messages':1000}
  with patch.object(self.pool.client,'read',return_value=updated):self.pool.refresh_definitions()
  self.assertEqual(self.pool.snapshot()['readyCount'],0);pending=self.pool.snapshot(include_pending=True)['portraitPending'][0]
  self.assertEqual(pending['avatarAsset']['review'],'pending');self.assertNotEqual(pending['avatarAsset']['sha256'],self.role['avatarAsset']['sha256']);self.assertEqual(self.pool.snapshot()['usedCount'],0)
 def test_automatic_review_evidence_must_match_cached_image(self):
  record=self.pool.state['ready'].pop();self.pool.state['portraitPending']=[record]
  with self.assertRaises(ValueError):self.pool.review_portrait(record['id'],record['avatarAsset']['sha256'],'photo',evidence={'sha256':'wrong'})
  self.assertEqual(self.pool.snapshot()['usedCount'],0);self.assertEqual(self.pool.snapshot()['readyCount'],0)
 def test_background_review_uses_real_callback_evidence_without_consuming_stock(self):
  record=self.pool.state['ready'].pop();record['avatarAsset']['review']='pending';self.pool.state['portraitPending']=[record]
  class Reviewer:
   def available(self):return True
   def snapshot(self):return {'modelVersion':'test-model'}
   def __call__(self,r):return {'decision':'accept','style':'photo','evidence':{'sha256':r['avatarAsset']['sha256'],'modelVersion':'test-model'}}
  self.pool.portrait_reviewer=Reviewer();self.assertEqual(self.pool.auto_review_pending(),1)
  saved=self.pool.get_record(record['id']);self.assertEqual(saved['avatarAsset']['autoReview']['modelVersion'],'test-model');self.assertEqual(self.pool.snapshot()['usedCount'],0)
 def test_deferred_portrait_stays_visible_reviewable_and_persistent(self):
  record=self.pool.state['ready'].pop();record['avatarAsset']['review']='pending';self.pool.state['portraitPending']=[record]
  self.assertEqual(self.pool.defer_portraits([record['id']]),1);self.assertEqual(len(self.pool.state['portraitPending']),0)
  self.assertEqual(self.pool.snapshot(include_pending=True)['portraitPendingCount'],1);self.assertEqual(self.pool.get_record(record['id'])['id'],record['id'])
  self.pool.close();self.pool=PoolStore(self.temp.name,FakeCatalogue(),target=1,reserve_target=1,taste=TasteProfile(self.temp.name))
  self.assertEqual(len(self.pool.state['portraitDeferred']),1)
  self.pool.review_portrait(record['id'],record['avatarAsset']['sha256'],'photo');self.assertEqual(self.pool.snapshot()['readyCount'],1);self.assertEqual(self.pool.snapshot()['usedCount'],0)
 def test_more_and_less_feedback_still_apply_to_quarantined_records(self):
  record=self.pool.state['ready'].pop();record['avatarAsset']['review']='pending';self.pool.state['portraitPending']=[record];self.pool.defer_portraits([record['id']])
  self.pool.feedback(record['id'],'more');self.assertEqual(self.pool.get_record(record['id'])['userPreference'],'more')
  self.pool.feedback(record['id'],'less');self.assertEqual(self.pool.snapshot()['portraitDeferredCount'],0);self.assertEqual(self.pool.snapshot()['usedCount'],0)

if __name__=='__main__':unittest.main()
