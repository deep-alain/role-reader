import copy,json,tempfile,unittest
from selection_rules import RuleStore,rank_records

def record(name,source='example',age=28,**stat):
 return {'name':name,'age':age,'metricsSource':source,'sourceMetrics':stat}

class RankingTests(unittest.TestCase):
 def test_real_zero_precedes_unknown_and_input_is_unchanged(self):
  values=[record('missing'),record('zero',views=0),record('popular',views=200)]
  before=copy.deepcopy(values);ranked=rank_records(values)
  self.assertEqual([r['name'] for r in ranked],['popular','zero','missing']);self.assertEqual(values,before)
  self.assertEqual(ranked[1]['ranking']['heatValue'],0)
 def test_browsing_and_message_counts_are_not_converted_or_compared(self):
  values=[record('views-second','views',views=20),record('messages-first','messages',messages=10**9),record('views-first','views',views=30),record('messages-second','messages',messages=100)]
  ranked=rank_records(values);by_name={r['name']:r for r in ranked}
  self.assertLess(ranked.index(by_name['views-first']),ranked.index(by_name['views-second']))
  self.assertEqual(by_name['messages-first']['ranking']['heatBasis'],'messages');self.assertNotIn('views',by_name['messages-first']['sourceMetrics'])
  self.assertEqual(by_name['views-first']['ranking']['sourcePosition'],1)
 def test_adult_verification_precedes_popularity_and_school_is_blocked(self):
  values=[record('unknown age',age='',views=1000),record('verified adult',views=1),record('minor',age=16,views=5000),{**record('18 but high school',age=18),'scenario':'High school'}]
  self.assertEqual([r['name'] for r in rank_records(values)],['verified adult','unknown age'])
 def test_true_nsfw_source_rating_precedes_heat_without_relabeling(self):
  values=[{**record('lower heat',views=1),'sourceRating':'NSFW','sourceLevel':3},{**record('higher heat',views=100),'sourceRating':'SFW'}]
  self.assertEqual(rank_records(values)[0]['name'],'lower heat');self.assertEqual(values[1]['sourceRating'],'SFW')
 def test_positive_threshold_requires_an_actual_counter(self):
  values=[record('unknown',messages=1000000),record('below',views=9),record('meets',views=10)]
  self.assertEqual([r['name'] for r in rank_records(values,{'minimumViews':10})],['meets'])
 def test_same_heat_uses_likes_and_keeps_favorites_distinct(self):
  values=[record('none',views=10),record('one',views=10,likes=1),record('zero',views=10,likes=0)]
  self.assertEqual(rank_records(values)[0]['name'],'one')
 def test_invalid_rules_are_atomic_and_persistence_is_real(self):
  with tempfile.TemporaryDirectory() as directory:
   store=RuleStore(directory);store.save({'minimumViews':123});before=store.snapshot()
   for changes in ({'minimumViews':True},{'minimumMessages':-1},{'minimumLikes':1.2},{'minimumViews':10**13},{'safeAvatarsOnly':False},{'unrecognized':1}):
    with self.assertRaises(ValueError):store.save(changes)
   self.assertEqual(store.snapshot(),before);self.assertEqual(RuleStore(directory).snapshot(),before)

if __name__=='__main__':unittest.main()
