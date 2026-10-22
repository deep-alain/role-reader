import copy,hashlib,io,json,math,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch

from pool_avatars import cache_avatar
from portrait_review import CLIP_EMBEDDING_VERSION,LocalPortraitReviewer,normalized_embedding
from test_pool_avatars import PNG


class CoverEmbeddingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.directory=self.root/'data';self.directory.mkdir()
  self.reviewer=LocalPortraitReviewer(self.directory,self.root)
  self.process=Mock();self.process.poll.return_value=None;self.process.wait.return_value=0;self.process.stdin=io.StringIO();self.process.stdout=io.StringIO()
  self.reviewer.process=self.process;self.reviewer.prepared=True
  self.record=self.record_for('a',PNG)
 def record_for(self,letter,data):
  record={'id':letter*24,'avatarSource':'https://botbooru.com/download/png/1'};record['avatarAsset']=cache_avatar(self.directory,record,data);return record
 def response(self,vector=None):return {'embedding':vector if vector is not None else [2.0]+[0.0]*511,'modelVersion':CLIP_EMBEDDING_VERSION}
 def tearDown(self):self.reviewer.close();self.temp.cleanup()
 def test_features_are_normalized_source_bound_and_never_an_approval(self):
  original=copy.deepcopy(self.record)
  with patch.object(self.reviewer,'_readline',return_value=self.response()):result=self.reviewer.embedding(self.record)
  self.assertEqual(len(result['embedding']),512);self.assertAlmostEqual(math.hypot(*result['embedding']),1)
  self.assertEqual(result['sha256'],hashlib.sha256(PNG).hexdigest());self.assertEqual(result['modelVersion'],CLIP_EMBEDDING_VERSION)
  self.assertNotIn('decision',result);self.assertNotIn('evidence',result);self.assertEqual(self.record,original)
  request=json.loads(self.process.stdin.getvalue());self.assertEqual(request['mode'],'embedding');self.assertNotIn('config',request)
 def test_unavailable_worker_returns_none_without_starting_setup(self):
  self.reviewer.prepared=False
  with patch.object(self.reviewer,'prepare') as prepare:self.assertIsNone(self.reviewer.embedding(self.record));prepare.assert_not_called()
  self.assertEqual(self.process.stdin.getvalue(),'')
 def test_source_mismatch_or_absence_never_reads_or_uses_cache(self):
  for source in ('https://botbooru.com/download/png/2',None,''):
   record=copy.deepcopy(self.record);record['avatarSource']=source
   self.assertIsNone(self.reviewer.embedding(record))
  self.assertEqual(self.process.stdin.getvalue(),'')
 def test_changed_actual_avatar_bytes_are_rejected_even_after_a_cache_hit(self):
  with patch.object(self.reviewer,'_readline',return_value=self.response()):self.assertIsNotNone(self.reviewer.embedding(self.record))
  (self.directory/'avatars'/self.record['avatarAsset']['file']).write_bytes(PNG+b'changed')
  with patch.object(self.reviewer,'_readline') as read:self.assertIsNone(self.reviewer.embedding(self.record));read.assert_not_called()
 def test_embedding_cache_coexists_with_review_cache_and_survives_safety_changes(self):
  review_key=self.reviewer.signature+':'+hashlib.sha256(PNG).hexdigest();decision={'decision':'defer','reason':'pending'};self.reviewer.cache[review_key]=decision
  with patch.object(self.reviewer,'_readline',return_value=self.response()):first=self.reviewer.embedding(self.record)
  self.assertEqual(self.reviewer.cache[review_key],decision)
  (self.root/'config').mkdir();(self.root/'config/automation.json').write_text(json.dumps({'maximumNsfwRisk':.001}))
  other=LocalPortraitReviewer(self.directory,self.root);other.prepared=True
  try:
   self.assertNotEqual(other.signature,self.reviewer.signature)
   with patch.object(other,'_readline') as read:self.assertEqual(other.embedding(self.record),first);read.assert_not_called()
  finally:other.close()
 def test_cache_is_bound_to_embedding_model_version_and_digest(self):
  key='embedding:'+CLIP_EMBEDDING_VERSION+':'+hashlib.sha256(PNG).hexdigest()
  for field,value in (('modelVersion','old-version'),('sha256','bad-digest'),('embedding',[0.0]*512)):
   cached={**self.response(),'sha256':hashlib.sha256(PNG).hexdigest()};cached[field]=value;self.reviewer.cache[key]=cached
   with patch.object(self.reviewer,'_readline',return_value=self.response()) as read:self.assertIsNotNone(self.reviewer.embedding(self.record));read.assert_called_once()
 def test_invalid_shapes_values_and_zero_vectors_fail_closed(self):
  invalid=[[],[1.0]*511,[1.0]*513,[0.0]*512,[float('nan')]+[0.0]*511,[float('inf')]+[0.0]*511,[True]+[0.0]*511,['1']+[0.0]*511,None]
  for vector in invalid:
   with self.subTest(vector=str(vector)[:30]),patch.object(self.reviewer,'_readline',return_value={'embedding':vector,'modelVersion':CLIP_EMBEDDING_VERSION}):self.assertIsNone(self.reviewer.embedding(self.record))
  self.assertEqual(self.reviewer.cache,{})
 def test_unexpected_worker_version_or_failure_cannot_create_cache(self):
  for response in ({'embedding':[1.0]*512,'modelVersion':'different'},{'decision':'accept'},{'decision':'defer','reason':'error'}):
   with patch.object(self.reviewer,'_readline',return_value=response):self.assertIsNone(self.reviewer.embedding(self.record))
  with patch.object(self.reviewer,'_readline',side_effect=RuntimeError('offline')):self.assertIsNone(self.reviewer.embedding(self.record))
  self.assertEqual(self.reviewer.cache,{})
 def test_embedding_and_review_requests_share_one_worker_lock(self):
  second=self.record_for('b',PNG+b'second');active=0;maximum=0;guard=threading.Lock();results=[]
  def read(_timeout):
   nonlocal active,maximum
   with guard:active+=1;maximum=max(maximum,active)
   request=json.loads(self.process.stdin.getvalue().splitlines()[-1]);time.sleep(.02)
   with guard:active-=1
   return self.response() if request.get('mode')=='embedding' else {'decision':'defer','reason':'uncertain'}
  with patch.object(self.reviewer,'_readline',side_effect=read):
   threads=[threading.Thread(target=lambda:results.append(self.reviewer.embedding(self.record))),threading.Thread(target=lambda:results.append(self.reviewer(second)))]
   for thread in threads:thread.start()
   for thread in threads:thread.join(2);self.assertFalse(thread.is_alive())
  self.assertEqual(maximum,1);self.assertEqual(len(results),2);self.assertEqual(len(self.process.stdin.getvalue().splitlines()),2)
  self.assertEqual(sum('embedding' in result for result in results),1);self.assertEqual(sum(result.get('decision')=='defer' for result in results),1)
 def test_normalization_handles_finite_non_unit_vectors(self):
  vector=normalized_embedding([3.0,4.0]+[0.0]*510)
  self.assertEqual(vector[:2],[.6,.8]);self.assertAlmostEqual(math.hypot(*vector),1)


if __name__=='__main__':unittest.main()
