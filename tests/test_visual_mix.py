import tempfile,unittest
from pool_store import PoolStore
from taste_profile import TasteProfile
from test_pool_store import FakeCatalogue

class VisualMixTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.taste=TasteProfile(self.temp.name);self.taste.set_realistic_target(20)
  self.pool=PoolStore(self.temp.name,FakeCatalogue(),target=5,reserve_target=1,taste=self.taste)
 def tearDown(self):self.pool.close();self.temp.cleanup()
 def record(self,key,real=False,approved=True):
  r=FakeCatalogue().read({'url':'https://example.org/'+key})['record'];digest=key*8
  r.update(id=key,avatarSource=r['avatar'],avatarAsset={'source':r['avatar'],'sha256':digest,'review':'approved' if approved else 'pending'},sourceMetrics={'messages':1000})
  self.taste.state['portraits'][digest]={'style':'glamour_portrait','medium':'photorealistic' if real else 'illustration'}
  return r
 def test_target_persists_without_imposing_any_gender_ratio(self):
  restored=TasteProfile(self.temp.name);self.assertEqual(restored.realistic_target(50),10)
  self.assertNotIn('gender',restored.snapshot());self.assertEqual(restored.snapshot()['targetRealisticPercent'],20)
  for invalid in (True,-1,101,20.5):
   with self.assertRaises(ValueError):restored.set_realistic_target(invalid)
 def test_glamour_style_is_independent_of_photo_medium(self):
  drawing=self.record('drawing');photo=self.record('photo',True)
  self.assertFalse(self.taste.is_realistic(drawing));self.assertTrue(self.taste.is_realistic(photo))
  self.taste.review(photo,'photo','illustration');self.assertFalse(self.taste.is_realistic(photo))
 def fill(self):
  self.pool.state['ready']=[self.record('ready'+str(i)) for i in range(5)]
  self.pool.state['reserve']=[self.record('reserve')]
 def test_missing_photo_slot_uses_approved_photo_candidate_without_discarding_reserve(self):
  self.fill();photo=self.record('new-photo',True);self.pool.state['portraitPending']=[photo]
  result=self.pool.consume('ready0');snap=self.pool.snapshot(True)
  self.assertEqual(result['replacementId'],photo['id']);self.assertEqual(snap['visualMix']['realisticCount'],1)
  self.assertEqual(snap['readyCount'],5);self.assertEqual(snap['reserveCount'],1);self.assertEqual(snap['usedCount'],1)
  self.assertEqual(snap['history'][0]['id'],'ready0')
 def test_unreviewed_photo_is_not_used_to_satisfy_the_ratio(self):
  self.fill();self.pool.state['portraitPending']=[self.record('unknown',True,False)]
  result=self.pool.consume('ready0');self.assertEqual(result['replacementId'],'reserve');self.assertEqual(self.pool.snapshot()['visualMix']['realisticCount'],0)
 def test_existing_target_does_not_overfill_photos_when_an_illustrated_character_is_used(self):
  self.fill();self.pool.state['ready'][0]=self.record('existing-photo',True)
  self.pool.state['portraitPending']=[self.record('new-photo',True)]
  result=self.pool.consume('ready1');self.assertEqual(result['replacementId'],'reserve');self.assertEqual(self.pool.snapshot()['visualMix']['realisticCount'],1)
 def test_using_a_photo_prioritizes_a_photo_replacement(self):
  self.fill();self.pool.state['ready'][0]=self.record('existing-photo',True)
  self.pool.state['portraitPending']=[self.record('new-photo',True)]
  self.assertEqual(self.pool.consume('existing-photo')['replacementId'],'new-photo')
 def test_setting_target_does_not_consume_or_replace_any_existing_role(self):
  self.fill();before=[r['id'] for r in self.pool.state['ready']]
  self.taste.set_realistic_target(40);snap=self.pool.snapshot(True)
  self.assertEqual([r['id'] for r in snap['ready']],before);self.assertEqual(snap['usedCount'],0)
 def test_refill_recalculates_ratio_after_each_approved_candidate(self):
  self.pool.state['ready']=[];self.pool.state['reserve']=[]
  self.pool.state['portraitPending']=[self.record('photo'+str(i),True) for i in range(3)]+[self.record('drawing'+str(i)) for i in range(6)]
  self.pool.refill_batch();snap=self.pool.snapshot()
  self.assertEqual(snap['readyCount'],5);self.assertEqual(snap['reserveCount'],1)
  self.assertEqual(sum(self.taste.is_realistic(r) for r in self.pool.state['ready']+self.pool.state['reserve']),1)

if __name__=='__main__':unittest.main()
