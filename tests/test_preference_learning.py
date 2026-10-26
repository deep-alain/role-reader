import copy,hashlib,json,tempfile,threading,unittest,urllib.request,urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path
from api_server import Handler
from pool_store import PoolStore
from pool_sources import PublicCatalogue
from selection_rules import rank_records,RuleStore
from taste_profile import TasteProfile

def role(key,theme='哥特与冷感反差',style='detailed_illustration',medium='illustration',approved=True,views=500):
 # Definitions differ beyond the name, so independent examples stay independent.
 return {'id':key,'source':'https://example.org/'+key,'name':key,'age':'28','category':'Adult, Female, Original, Knight',
  'prompt':('A thoughtful independent adult with her own occupation and daily routine. '*5)+'Occupation reference '+hashlib.sha256(key.encode()).hexdigest(),
  'greet':'At the end of a long day, she greets you at the doorway and asks about your evening. '+key,
  'scenario':'At home.','intro':'An adult character.','gender':'Female','avatar':'https://example.org/'+key+'.png',
  'avatarSource':'https://example.org/'+key+'.png','avatarAsset':{'sha256':'digest-'+key,'source':'https://example.org/'+key+'.png','review':'approved' if approved else 'pending'},
  'toneMatches':[theme],'sourceMetrics':{'views':views},'style':style,'medium':medium,'fieldSources':{}}

class FeedbackTests(unittest.TestCase):
 def test_new_user_realistic_default_does_not_override_saved_preferences(self):
  with tempfile.TemporaryDirectory() as directory:
   taste=TasteProfile(directory,default_realistic_percent=20)
   self.assertEqual(taste.state['targetRealisticPercent'],20)
   taste.set_realistic_target(35)
   self.assertEqual(TasteProfile(directory,default_realistic_percent=20).state['targetRealisticPercent'],35)
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.taste=TasteProfile(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def reviewed(self,key,**kwargs):
  r=role(key,**kwargs);self.taste.review(r,r['style'],r['medium']);return r
 def vote(self,key,choice='more',**kwargs):
  r=self.reviewed(key,**kwargs);self.taste.feedback(r,choice);return r
 def test_positive_votes_promote_a_new_similar_role_not_only_the_clicked_one(self):
  self.vote('like1');self.vote('like2')
  similar=self.reviewed('new-goth');other=self.reviewed('new-roommate',theme='室友与同居')
  a=self.taste.evaluate(similar)[2];b=self.taste.evaluate(other)[2]
  self.assertGreater(a['preferenceLearning']['score'],b['preferenceLearning']['score']);self.assertIn('哥特',a['learnedPreferenceReason'])
  self.assertEqual(rank_records([b,a])[0]['id'],a['id']);self.assertEqual(a['sourceMetrics']['views'],500)
 def test_one_dislike_blocks_the_type_without_waiting_for_a_second_vote(self):
  first=self.vote('no1','less');similar=self.reviewed('similar')
  self.assertEqual(self.taste.evaluate(first)[0],'reject')
  self.assertEqual(self.taste.evaluate(similar)[0],'reject')
  self.assertEqual(self.taste.model()['blockedTypes'][0]['blockedBy'],1)
  self.vote('no2','less');self.assertEqual(self.taste.evaluate(similar)[0],'reject')
  neutral=self.reviewed('neutral',theme='职场与名流',style='photo',medium='photorealistic')
  self.assertEqual(self.taste.evaluate(neutral)[0],'accept')
 def test_repeated_clicks_overwrite_a_vote_and_do_not_manufacture_support(self):
  r=self.vote('same')
  for _ in range(5):self.taste.feedback(r,'more')
  learned=self.taste.snapshot()['learning'];self.assertEqual(learned['uniqueExamples'],1)
  self.assertEqual(self.taste.model()['stats']['theme:哥特与冷感反差']['more'],1)
  self.taste.feedback(r,'less');self.assertEqual(self.taste.model()['stats']['theme:哥特与冷感反差']['more'],0)
 def test_mirror_votes_merge_and_latest_choice_controls_the_shared_training_example(self):
  a=self.vote('original');b=copy.deepcopy(a);b.update(source='https://mirror.example/renamed',name='Renamed',id='mirror')
  self.taste.feedback(b,'more');self.assertEqual(self.taste.snapshot()['learning']['uniqueExamples'],1)
  self.taste.feedback(b,'less');self.assertEqual(self.taste.model()['stats']['theme:哥特与冷感反差']['less'],1)
  self.assertEqual(self.taste.evaluate(a)[0],'reject')
 def test_same_definition_is_blocked_even_with_a_different_source_and_image(self):
  original=self.vote('original','less');mirror=self.reviewed('mirror')
  mirror['prompt']=original['prompt'];mirror['greet']=original['greet']
  self.assertEqual(self.taste.evaluate(mirror)[0],'reject')
 def test_rejected_cartoon_feedback_does_not_penalize_unrelated_story_themes(self):
  for i in range(2):self.vote('bad'+str(i),'less',style='chibi_cartoon')
  self.assertNotIn('theme:哥特与冷感反差',self.taste.model()['stats'])
  self.assertEqual(self.taste.evaluate(self.reviewed('fine-goth'))[2]['preferenceLearning']['score'],0)
 def test_learning_never_bypasses_avatar_review_or_heat_floors(self):
  self.vote('like1');self.vote('like2')
  unknown=self.reviewed('unknown',approved=False);weak=self.reviewed('weak',views=1)
  self.assertEqual(self.taste.evaluate(unknown)[0],'pending');self.assertEqual(self.taste.evaluate(weak)[0],'reject')
 def test_legacy_feedback_is_backfilled_from_local_records_and_survives_restart(self):
  r=self.reviewed('legacy');self.taste.state['feedback'][r['source']]={'choice':'more','name':r['name'],'sha256':r['avatarAsset']['sha256'],'at':'2026-01-01'};self.taste._save()
  self.assertTrue(self.taste.hydrate_feedback([r]));self.assertFalse(self.taste.hydrate_feedback([r]))
  restored=TasteProfile(self.temp.name);self.assertEqual(restored.snapshot()['learning']['uniqueExamples'],1)
  self.assertIn('theme:哥特与冷感反差',restored.state['feedback'][r['source']]['features'])
 def test_reclassifying_a_portrait_updates_its_vote_features(self):
  r=self.vote('portrait');self.taste.review(r,'photo')
  vote=self.taste.state['feedback'][r['source']];self.assertIn('medium:photorealistic',vote['features']);self.assertNotIn('style:detailed_illustration',vote['features'])
 def test_preferred_queries_require_two_independent_votes_and_keep_exploration(self):
  self.vote('one');self.assertIsNone(self.taste.discovery_query(0))
  self.vote('two');queries=[self.taste.discovery_query(i) for i in range(8)]
  self.assertEqual(queries[3],None);self.assertEqual(queries[7],None);self.assertEqual(sum(q=='goth' for q in queries),6)
 def test_visual_query_respects_excluded_story_type_and_undo(self):
  self.vote('photo1',style='photo',medium='photorealistic')
  self.vote('photo2',style='photo',medium='photorealistic')
  excluded=self.vote('romance','less',theme='恋人关系冲突')
  self.assertIn('realistic girlfriend',self.taste.model()['queries'])
  self.taste.feedback(excluded,'more')
  self.assertIn('realistic girlfriend',self.taste.model()['queries'])
 def test_query_exclusion_requires_the_entire_type_combination(self):
  self.vote('photo1',style='photo',medium='photorealistic')
  self.vote('photo2',style='photo',medium='photorealistic')
  excluded=self.reviewed('combined',theme='恋人关系冲突')
  excluded['toneMatches'].append('室友与同居');self.taste.feedback(excluded,'less')
  self.assertIn('realistic girlfriend',self.taste.model()['queries'])

class FakeCatalogue:
 sources=('chub',);supports_query_override=True
 def __init__(self):self.calls=[]
 def catalogue(self,source,page=1,limit=20,query=None):
  self.calls.append((source,page,query));return {'items':[],'nextPage':page+1}

class ShelfFeedbackTests(unittest.TestCase):
 reviewed=FeedbackTests.reviewed
 vote=FeedbackTests.vote
 def setUp(self):
  FeedbackTests.setUp(self);self.client=FakeCatalogue();self.pool=PoolStore(self.temp.name,self.client,target=2,reserve_target=1,taste=self.taste)
 def tearDown(self):self.pool.close();FeedbackTests.tearDown(self)
 def test_replacement_uses_learned_similarity_and_keeps_usage_idempotent(self):
  self.vote('seed1');self.vote('seed2');self.pool.state['ready']=[self.reviewed('use'),self.reviewed('keep')]
  self.pool.state['reserve']=[self.reviewed('neutral',theme='职场与名流',style='photo',medium='photorealistic')]
  self.pool.state['portraitPending']=[self.reviewed('preferred-goth')]
  result=self.pool.consume('use');self.assertEqual(result['replacementId'],'preferred-goth');self.assertEqual(self.pool.snapshot()['usedCount'],1)
  self.assertTrue(self.pool.consume('use')['alreadyUsed']);self.assertEqual(self.pool.snapshot()['usedCount'],1)
 def test_personalized_paging_is_independent_of_original_rotation(self):
  self.vote('seed1');self.vote('seed2')
  for _ in range(4):self.pool._catalogue('chub',7,20)
  self.assertEqual(self.client.calls,[('chub',1,'goth'),('chub',2,'goth'),('chub',3,'goth'),('chub',7,None)])
 def test_completed_preference_search_falls_back_and_refreshes_after_cooldown(self):
  self.vote('seed1');self.vote('seed2')
  original=self.client.catalogue
  def catalogue(source,page=1,limit=20,query=None):
   result=original(source,page,limit,query)
   if query:result['nextPage']=None
   return result
  self.client.catalogue=catalogue
  self.pool._catalogue('chub',7,20);self.pool._catalogue('chub',7,20)
  self.assertEqual(self.client.calls,[('chub',1,'goth'),('chub',7,None)])
  self.pool.state['preferenceQueryExhausted']['chub|goth']=0
  self.pool._catalogue('chub',8,20)
  self.assertEqual(self.client.calls[-1],('chub',1,'goth'))
 def test_learned_photo_preference_does_not_override_the_requested_mix(self):
  self.vote('seed1',style='photo',medium='photorealistic');self.vote('seed2',style='photo',medium='photorealistic')
  self.pool.target=5;self.taste.set_realistic_target(20)
  self.pool.state['ready']=[self.reviewed('existing-photo',style='photo',medium='photorealistic')]+[self.reviewed('ready'+str(i),theme='室友与同居') for i in range(4)]
  self.pool.state['reserve']=[self.reviewed('illustration',theme='职场与名流')]
  self.pool.state['portraitPending']=[self.reviewed('preferred-photo',style='photo',medium='photorealistic')]
  self.assertEqual(self.pool.consume('ready0')['replacementId'],'illustration')
  self.assertEqual(self.pool.snapshot()['visualMix']['realisticCount'],1)
 def test_builtin_override_reaches_real_search_parameters_without_enabling_nsfw(self):
  urls=[]
  def fetch(url):urls.append(url);return json.dumps({'data':{'nodes':[]}}).encode(),'application/json'
  PublicCatalogue(fetch).catalogue('chub',3,20,query='goth');params=urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)
  self.assertEqual(params['search'],['goth']);self.assertEqual(params['page'],['3']);self.assertEqual(params['nsfw'],['false'])
  with self.assertRaises(ValueError):PublicCatalogue(fetch).catalogue('chub',1,20,query='arbitrary unsupported query')
 def test_real_feedback_endpoint_changes_other_role_scores_without_consuming(self):
  a=self.reviewed('like1');b=self.reviewed('like2');similar=self.reviewed('candidate')
  self.pool.state['ready']=[a,b];self.pool.state['reserve']=[];self.pool.state['portraitPending']=[similar]
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.pool=self.pool;server.rules=RuleStore(self.temp.name)
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   for r in (a,b):
    request=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/pool/feedback',data=json.dumps({'id':r['id'],'choice':'more'}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=5) as response:result=json.load(response)
   self.assertEqual(result['pool']['usedCount'],0);self.assertEqual(result['learning']['uniqueExamples'],2)
   candidate=result['pool']['portraitPending'][0];self.assertGreater(candidate['preferenceLearning']['score'],0)
   self.assertIn('哥特',candidate['preferenceReason']);self.assertEqual(result['pool']['readyCount'],2)
   for r in (a,b):
    request=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/pool/feedback',data=json.dumps({'id':r['id'],'choice':'less'}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=5) as response:result=json.load(response)
   self.assertEqual(result['pool']['usedCount'],0);self.assertEqual(result['pool']['portraitPending'],[])
   self.assertEqual(result['pool']['ready'],[]);self.assertIn('排除此类',result['effect']);self.assertTrue(result['learning']['blockedTypes'])
  finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
