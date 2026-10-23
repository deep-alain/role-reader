import tempfile,unittest,copy,base64
from pathlib import Path
from unittest.mock import patch
from pool_avatars import validate_avatar_url,cache_avatar,avatar_file,fetch_record_avatar
from pool_store import PoolStore
from test_pool_store import FakeCatalogue

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
class AvatarTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.store=PoolStore(self.temp.name,FakeCatalogue());self.role=self.store.client.read({'url':'https://example.org/avatar-test'})
 def tearDown(self):self.store.close();self.temp.cleanup()
 def test_source_png_is_retained_even_while_covered(self):
  self.role['image']=PNG;self.assertTrue(self.store.accept(self.role));r=self.store.snapshot()['ready'][0]
  self.assertEqual(r['avatarVisibility'],'covered');self.assertEqual(avatar_file(self.temp.name,r),(PNG,'image/png'));self.assertEqual(r['avatarAsset']['review'],'pending')
 def test_reviewed_original_survives_restart_and_can_be_hidden(self):
  self.role['image']=PNG;self.store.accept(self.role);r=self.store.snapshot()['ready'][0];digest=r['avatarAsset']['sha256']
  self.store.review_avatar(r['id'],digest,True);self.store.close();self.store=PoolStore(self.temp.name,FakeCatalogue());saved=self.store.get_record(r['id'])
  self.assertEqual(saved['avatarVisibility'],'source');self.assertEqual(saved['avatar'],r['avatarSource']);self.assertEqual(self.store.snapshot()['usedCount'],0)
  hidden=self.store.review_avatar(r['id'],digest,False);self.assertEqual(hidden['avatarVisibility'],'covered')
 def test_changed_image_cannot_reuse_old_approval(self):
  r={'id':'a'*24,'avatarSource':'https://botbooru.com/download/png/1'};r['avatarAsset']=cache_avatar(self.temp.name,r,PNG);r['avatarAsset']['review']='approved'
  self.assertEqual(cache_avatar(self.temp.name,r,PNG+b'changed')['review'],'pending')
  r['avatarSource']='https://botbooru.com/download/png/2';self.assertEqual(cache_avatar(self.temp.name,r,PNG)['review'],'pending')
 def test_invalid_data_or_private_url_is_rejected(self):
  for url in ['http://127.0.0.1/a','https://avatars.charhub.io.evil.example/avatars/a','https://botbooru.com/download/png/1?token=x','https://user:password@botbooru.com/download/png/1','https://avatars.charhub.io/avatars/%2e%2e/a']:
   with self.assertRaises(ValueError):validate_avatar_url(url)
  with self.assertRaises(ValueError):cache_avatar(self.temp.name,{'id':'a'*24,'avatarSource':'x'},b'<html>error</html>')
 def test_missing_primary_uses_same_api_alternate_avatar(self):
  r={'source':'https://chub.ai/characters/a/b','avatarSource':'https://avatars.charhub.io/avatars/a/b/chara_card_v2.png','raw':{'apiResponse':{'node':{'avatar_url':'https://avatars.charhub.io/avatars/a/b/avatar.webp'}}}}
  with patch('pool_avatars.fetch_avatar',side_effect=[OSError('404'),PNG]) as fetch:
   data,url=fetch_record_avatar(r);self.assertEqual(data,PNG);self.assertTrue(url.endswith('avatar.webp'));self.assertEqual(fetch.call_count,2)
 def test_current_image_hash_required_for_review(self):
  self.role['image']=PNG;self.store.accept(self.role);r=self.store.snapshot()['ready'][0]
  with self.assertRaises(ValueError):self.store.review_avatar(r['id'],'stale',True)
 def test_background_sync_downloads_avatar_without_consuming_bot(self):
  self.store.accept(self.role)
  with patch('pool_store.fetch_record_avatar',return_value=(PNG,'https://botbooru.com/download/png/1')):self.store.sync_avatars()
  snap=self.store.snapshot();self.assertEqual(snap['readyCount'],1);self.assertEqual(snap['usedCount'],0);self.assertEqual(avatar_file(self.temp.name,snap['ready'][0])[0],PNG)
 def test_background_repairs_a_missing_or_corrupt_cached_file(self):
  self.role['image']=PNG;self.store.accept(self.role);r=self.store.snapshot()['ready'][0]
  path=Path(self.temp.name)/'avatars'/r['avatarAsset']['file']
  for missing in (True,False):
   if missing:path.unlink()
   else:path.write_bytes(b'corrupt')
   with patch('pool_store.fetch_record_avatar',return_value=(PNG,r['avatarSource'])) as fetch:self.store.sync_avatars();fetch.assert_called_once()
   self.assertEqual(avatar_file(self.temp.name,self.store.get_record(r['id']))[0],PNG)
  self.assertEqual(self.store.snapshot()['usedCount'],0)
 def test_manual_reread_preserves_approval_only_for_identical_source_bytes(self):
  self.role['image']=PNG;self.store.accept(self.role);r=self.store.snapshot()['ready'][0]
  self.store.review_avatar(r['id'],r['avatarAsset']['sha256'],True)
  with patch('pool_store.fetch_record_avatar',return_value=(PNG,r['avatarSource'])):self.store.retry_avatar(r['id'])
  self.assertEqual(self.store.get_record(r['id'])['avatarAsset']['review'],'approved')
  with patch('pool_store.fetch_record_avatar',return_value=(PNG+b'changed',r['avatarSource'])):self.store.retry_avatar(r['id'])
  self.assertEqual(self.store.get_record(r['id'])['avatarAsset']['review'],'pending')
  self.assertEqual(self.store.snapshot()['usedCount'],0)
 def test_failed_reread_keeps_existing_cached_image(self):
  self.role['image']=PNG;self.store.accept(self.role);r=self.store.snapshot()['ready'][0]
  with patch('pool_store.fetch_record_avatar',side_effect=OSError('network unavailable')):
   with self.assertRaisesRegex(ValueError,'来源头像读取失败'):self.store.retry_avatar(r['id'])
  self.assertEqual(avatar_file(self.temp.name,self.store.get_record(r['id']))[0],PNG)
 def test_background_repair_does_not_overwrite_a_concurrent_review(self):
  self.role['image']=PNG;self.store.accept(self.role);r=self.store.snapshot()['ready'][0]
  (Path(self.temp.name)/'avatars'/r['avatarAsset']['file']).unlink()
  def fetch(record):
   self.store.review_avatar(r['id'],r['avatarAsset']['sha256'],True)
   return PNG,r['avatarSource']
  with patch('pool_store.fetch_record_avatar',side_effect=fetch):self.store.sync_avatars()
  self.assertEqual(self.store.get_record(r['id'])['avatarAsset']['review'],'approved')

if __name__=='__main__':unittest.main()
