import json,unittest,urllib.parse,tempfile
from source_metrics import metrics,source_rating,rank_items
from pool_sources import PublicCatalogue
from pool_store import PoolStore
from test_pool_store import FakeCatalogue

class MetricsTests(unittest.TestCase):
 def test_downloads_are_not_likes_and_zero_is_not_missing(self):
  result=metrics('chub',{'starCount':77,'n_favorites':0,'nChats':8})
  self.assertEqual(result['downloads'],77);self.assertEqual(result['favorites'],0);self.assertNotIn('likes',result);self.assertNotIn('views',result);self.assertIsNone(result['messages'])
 def test_unknown_metrics_remain_unknown(self):
  self.assertEqual(metrics('botbooru',{'views':0,'favorite_count':True})['views'],0);self.assertIsNone(metrics('botbooru',{'favorite_count':True})['favorites']);self.assertNotIn('downloads',metrics('charavault',{'avg_rating':4.5}))
 def test_original_rating_is_distinct_from_age_or_local_adult_filter(self):
  self.assertEqual(source_rating('chub',{'topics':['Adult','Roommate']}),'SFW');self.assertEqual(source_rating('botbooru',{'tags':[{'name':'nsfw'}]}),'NSFW');self.assertEqual(source_rating('botbooru',{'tags':[]}),'unknown');self.assertEqual(source_rating('charavault',{'nsfw':False}),'SFW')
 def test_source_pins_cannot_override_real_counts(self):
  items=[{'sourceMetrics':{'favorites':v}} for v in [875,5,102,None,0]]
  self.assertEqual([r['sourceMetrics']['favorites'] for r in rank_items(items,'favorites')],[875,102,5,0,None])
 def test_popular_catalogues_preserve_metrics_with_the_right_ids(self):
  urls=[]
  def fetch(url):
   urls.append(url);return json.dumps({'data':{'nodes':[{'fullPath':'a/pinned','n_favorites':5,'topics':['Adult']},{'fullPath':'a/top','n_favorites':102,'topics':['Adult']},{'fullPath':'invalid','n_favorites':999}], 'count':3}}).encode(),'application/json'
  result=PublicCatalogue(fetch).catalogue('chub',1,20);self.assertEqual(result['items'][0]['url'],'https://chub.ai/characters/a/top');self.assertEqual(result['items'][0]['sourceMetrics']['favorites'],102);self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)['sort'],['n_favorites'])
 def test_booru_views_uses_all_time_and_real_view_field(self):
  urls=[]
  def fetch(url):
   urls.append(url);return json.dumps({'posts':[{'id':1,'views':3,'favorite_count':2,'tags':[]},{'id':2,'views':30,'favorite_count':0,'tags':[]}]}).encode(),'application/json'
  result=PublicCatalogue(fetch).catalogue('botbooru',1,20);self.assertTrue(urls[0].endswith('time_window=all'));self.assertEqual(result['items'][0]['url'],'https://botbooru.com/character/2')
 def test_refreshing_metrics_does_not_consume_or_change_original_definition(self):
  with tempfile.TemporaryDirectory() as directory:
   client=FakeCatalogue();client.read_metadata=lambda r:{'sourceMetrics':{'views':123},'sourceRating':'SFW','metricsAt':'2026-10-16T00:00:00+00:00'}
   store=PoolStore(directory,client);store.accept(client.read({'url':'https://example.org/test'}));old=store.snapshot()['ready'][0];store.refresh_metrics();new=store.snapshot()['ready'][0]
   self.assertEqual(new['sourceMetrics']['views'],123);self.assertEqual(new['prompt'],old['prompt']);self.assertEqual(new['id'],old['id']);self.assertEqual(store.snapshot()['usedCount'],0);store.close()

if __name__=='__main__':unittest.main()
