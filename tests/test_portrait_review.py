import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from portrait_review import decide,LocalPortraitReviewer,PROMPTS
from test_pool_avatars import PNG
from pool_avatars import cache_avatar

class PortraitDecisionTests(unittest.TestCase):
 def scores(self,winner='photo',gap=.08):return {name:(.36 if name==winner else .36-gap) for name in PROMPTS}
 def test_only_clear_low_risk_portraits_are_automatically_accepted(self):
  result=decide(.01,self.scores());self.assertEqual(result['decision'],'accept');self.assertEqual(result['medium'],'photorealistic')
  self.assertIn('margin',result['evidence']);self.assertNotIn('age',result)
 def test_a_source_style_cannot_override_model_risk(self):
  result=decide(.8,self.scores('detailed_illustration'));self.assertEqual(result['decision'],'defer')
 def test_ambiguous_or_weak_matches_never_auto_approve(self):
  self.assertEqual(decide(.001,self.scores(gap=.001))['decision'],'defer')
  self.assertEqual(decide(.001,{k:.1 for k in PROMPTS})['decision'],'defer')
 def test_explicit_and_disallowed_styles_are_filtered(self):
  for kind in ('explicit','no_person','default_icon','chibi_cartoon','faint_sketch'):
   self.assertEqual(decide(.01,self.scores(kind))['decision'],'reject')
 def test_invalid_model_values_never_approve(self):
  for risk in (-1,2,float('nan')):self.assertEqual(decide(risk,self.scores())['decision'],'defer')
  values=self.scores();values['photo']=float('inf');self.assertEqual(decide(.01,values)['decision'],'defer')
 def test_confusion_between_valid_portrait_styles_does_not_block_a_clear_person(self):
  scores=self.scores();scores['rendered_portrait']=.359
  result=decide(.01,scores);self.assertEqual(result['decision'],'accept');self.assertEqual(result['style'],'portrait');self.assertIsNone(result['medium'])

class ReviewerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.directory=self.root/'data';self.directory.mkdir();self.reviewer=LocalPortraitReviewer(self.directory,self.root)
 def tearDown(self):self.reviewer.close();self.temp.cleanup()
 def test_missing_models_are_reported_and_do_not_approve(self):
  self.assertFalse(self.reviewer.available());self.assertEqual(self.reviewer({})['decision'],'defer')
 def test_explicitly_disabled_setup_never_launches_an_installer(self):
  self.reviewer.disabled=True
  with patch('portrait_review.subprocess.Popen') as process:self.assertFalse(self.reviewer.prepare());process.assert_not_called()
 def test_cached_result_is_bound_to_model_signature_and_actual_image_bytes(self):
  record={'id':'a'*24,'avatarSource':'https://botbooru.com/download/png/1'};record['avatarAsset']=cache_avatar(self.directory,record,PNG)
  digest=hashlib.sha256(PNG).hexdigest();key=self.reviewer.signature+':'+digest
  self.reviewer.cache[key]={'decision':'accept','style':'photo'};self.reviewer.prepared=True
  self.assertEqual(self.reviewer(record)['decision'],'accept')
  path=self.directory/'avatars'/record['avatarAsset']['file'];path.write_bytes(PNG+b'changed')
  self.assertEqual(self.reviewer(record)['decision'],'defer')
 def test_configuration_changes_invalidate_old_decisions(self):
  (self.root/'config').mkdir();(self.root/'config/automation.json').write_text(json.dumps({'maximumNsfwRisk':.005}))
  other=LocalPortraitReviewer(self.directory,self.root);self.assertNotEqual(other.signature,self.reviewer.signature);other.close()
 def test_temporary_worker_failures_are_not_cached_as_image_decisions(self):
  from unittest.mock import Mock
  record={'id':'a'*24,'avatarSource':'https://botbooru.com/download/png/1'};record['avatarAsset']=cache_avatar(self.directory,record,PNG)
  self.reviewer.prepared=True;self.reviewer.process=Mock();self.reviewer.process.poll.return_value=None
  key=self.reviewer.signature+':'+record['avatarAsset']['sha256']
  self.reviewer.cache[key]={'decision':'defer','reason':'old transient failure'}
  with patch.object(self.reviewer,'_readline',return_value={'decision':'defer','reason':'worker failed'}) as read:
   self.reviewer(record);self.reviewer(record)
   self.assertEqual(read.call_count,2);self.assertNotIn(key,self.reviewer.cache)
 def test_changed_source_cannot_reuse_cached_approval(self):
  record={'id':'a'*24,'avatarSource':'https://botbooru.com/download/png/1'};record['avatarAsset']=cache_avatar(self.directory,record,PNG)
  self.reviewer.cache[self.reviewer.signature+':'+record['avatarAsset']['sha256']]={'decision':'accept','style':'photo'}
  self.reviewer.prepared=True;record['avatarSource']='https://botbooru.com/download/png/2'
  self.assertEqual(self.reviewer(record)['decision'],'defer')

if __name__=='__main__':unittest.main()
