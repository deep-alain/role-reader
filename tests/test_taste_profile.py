import tempfile,unittest
from pathlib import Path
from taste_profile import TasteProfile
from pool_store import PoolStore
from selection_rules import rank_records
from test_pool_store import FakeCatalogue
from test_pool_avatars import PNG

class TasteTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.client=FakeCatalogue();self.taste=TasteProfile(self.temp.name);self.pool=PoolStore(self.temp.name,self.client,target=2,reserve_target=1,taste=self.taste)
 def tearDown(self):self.pool.close();self.temp.cleanup()
 def candidate(self,key,**stat):
  result=self.client.read({'url':'https://example.org/'+key});result['image']=PNG;result['record'].update(sourceMetrics=stat,metricsSource='example');return result
 def test_unknown_portrait_never_counts_as_ready_and_review_does_not_consume(self):
  self.assertFalse(self.pool.accept(self.candidate('one',messages=200)));snap=self.pool.snapshot(include_pending=True)
  self.assertEqual(snap['readyCount'],0);self.assertEqual(snap['portraitPendingCount'],1);r=snap['portraitPending'][0]
  self.pool.review_portrait(r['id'],r['avatarAsset']['sha256'],'detailed_illustration');after=self.pool.snapshot()
  self.assertEqual(after['readyCount'],1);self.assertEqual(after['portraitPendingCount'],0);self.assertEqual(after['usedCount'],0);self.assertEqual(after['ready'][0]['avatarVisibility'],'source')
  with self.assertRaises(ValueError):self.pool.review_portrait(r['id'],r['avatarAsset']['sha256'],'detailed_illustration')
 def test_weak_messages_cannot_be_rescued_by_large_downloads_or_unknown_zero(self):
  for name,stat in [('low',{'messages':59,'downloads':99999}),('zero',{'messages':0}),('views',{'views':79})]:self.assertFalse(self.pool.accept(self.candidate(name,**stat)))
  self.assertEqual(self.pool.snapshot()['portraitPendingCount'],0)
 def test_same_rejected_image_is_blocked_across_sources_and_restarts(self):
  self.pool.accept(self.candidate('one',messages=200));r=self.pool.snapshot(include_pending=True)['portraitPending'][0]
  self.pool.review_portrait(r['id'],r['avatarAsset']['sha256'],'default_icon')
  self.assertFalse(self.pool.accept(self.candidate('other',messages=9999)));self.assertEqual(self.pool.snapshot()['readyCount'],0);self.assertEqual(self.pool.snapshot()['portraitPendingCount'],0)
  restored=TasteProfile(self.temp.name);self.assertEqual(restored.state['portraits'][r['avatarAsset']['sha256']]['style'],'default_icon')
 def test_dislike_retires_without_marking_used_and_keeps_original_content(self):
  self.pool.accept(self.candidate('one',messages=200));r=self.pool.snapshot(include_pending=True)['portraitPending'][0];self.pool.review_portrait(r['id'],r['avatarAsset']['sha256'],'glamour_portrait')
  self.pool.feedback(r['id'],'less');s=self.pool.snapshot();self.assertEqual(s['usedCount'],0);self.assertEqual(s['readyCount'],0);self.assertTrue(self.pool.state['retired'][-1]['prompt']);self.assertFalse(self.pool.accept(self.candidate('one',messages=99999)))
 def test_fine_anime_is_allowed_while_fnf_metadata_is_rejected(self):
  a=self.candidate('adult-anime',messages=200)['record'];a['category']='Adult, Female, Anime, Roommate';self.assertEqual(self.taste.evaluate(a)[0],'pending')
  a['category']='Adult, Friday Night Funkin';self.assertEqual(self.taste.evaluate(a)[0],'reject')
 def test_changed_image_requires_a_new_review(self):
  self.pool.accept(self.candidate('one',messages=200));r=self.pool.snapshot(include_pending=True)['portraitPending'][0];self.pool.review_portrait(r['id'],r['avatarAsset']['sha256'],'photo')
  from PIL import Image
  from io import BytesIO
  image=BytesIO();Image.new('RGB',(16,16),'blue').save(image,format='PNG')
  newer=self.candidate('two',messages=200);newer['image']=image.getvalue();self.pool.accept(newer);self.assertEqual(self.pool.snapshot()['portraitPendingCount'],1)
 def test_source_nsfw_rating_precedes_heat_and_portrait_preference(self):
  values=[{'name':'hotter count','age':'28','sourceMetrics':{'views':99999},'sourceRating':'NSFW'},{'name':'preferred portrait','age':'28','sourceMetrics':{'views':100},'preferencePriority':2}]
  self.assertEqual(rank_records(values)[0]['name'],'hotter count')

if __name__=='__main__':unittest.main()
