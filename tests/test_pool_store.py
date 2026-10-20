import concurrent.futures,json,tempfile,unittest
from pathlib import Path
from pool_store import PoolStore
from pool_sources import card_to_record,chub_record

class FakeCatalogue:
 def __init__(self):self.reads=0
 def catalogue(self,source,page=1,limit=20):
  return {'items':[{'source':source,'url':f'https://example.org/{source}/{i}'} for i in range((page-1)*limit,page*limit)],'nextPage':page+1}
 def read(self,item):
  self.reads+=1;key=item['url']
  return {'record':card_to_record({'name':'Bot '+key,'age':28,'description':'A thoughtful adult roommate. '+('A patient, independent person with work, friends and clear boundaries. '*4)+key,'first_mes':'The evening rain has stopped. Your roommate sets a warm cup on the table and asks about your day. '+key,'scenario':'In a workshop.','tags':['Original','Adult','Roommate']},key,'https://example.org/a.png')}

class ShelfTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.client=FakeCatalogue();self.store=PoolStore(self.temp.name,self.client)
 def tearDown(self):self.store.close();self.temp.cleanup()
 def fill(self):
  for _ in range(30):
   self.store.refill_batch()
   if self.store.snapshot()['reserveCount']==5:break
  self.assertEqual(self.store.snapshot()['readyCount'],50);self.assertEqual(self.store.snapshot()['reserveCount'],5)
 def test_fill_50_plus_5_with_real_content_and_no_duplicates(self):
  self.fill();snap=self.store.snapshot();self.assertEqual(len({r['source'] for r in snap['ready']}),50);self.assertTrue(all(r['prompt'] and r['greet'] for r in snap['ready']));self.assertLessEqual(self.client.reads,57)
 def test_use_one_replaces_one_immediately_and_replenishes_reserve(self):
  self.fill();before=self.store.snapshot();old=before['ready'][7]['id'];result=self.store.consume(old);after=self.store.snapshot(True)
  self.assertTrue(result['replacementId']);self.assertEqual(after['readyCount'],50);self.assertEqual(after['reserveCount'],4);self.assertEqual(after['usedCount'],1);self.assertEqual(after['ready'][7]['id'],result['replacementId']);self.assertNotIn(old,[r['id'] for r in after['ready']]);self.assertEqual(after['history'][0]['id'],old)
  self.store.refill_batch();self.assertEqual(self.store.snapshot()['reserveCount'],5)
 def test_double_click_is_idempotent(self):
  self.fill();old=self.store.snapshot()['ready'][0]['id'];a=self.store.consume(old);b=self.store.consume(old);self.assertFalse(a['alreadyUsed']);self.assertTrue(b['alreadyUsed']);self.assertEqual(self.store.snapshot()['usedCount'],1);self.assertEqual(self.store.snapshot()['reserveCount'],4)
 def test_restart_preserves_history_and_prevents_used_bot_returning(self):
  self.fill();old=self.store.snapshot()['ready'][0];self.store.consume(old['id']);self.store.close();restored=PoolStore(self.temp.name,self.client);self.store=restored;self.assertEqual(restored.snapshot()['usedCount'],1);self.assertFalse(restored.accept({'record':old}));self.assertEqual(restored.snapshot()['readyCount'],50)
 def test_reject_empty_prompt_and_cross_site_identical_content(self):
  r=self.client.read({'url':'https://example.org/1'})['record'];self.assertTrue(self.store.accept({'record':r}));duplicate=dict(r,source='https://another.example/same');self.assertFalse(self.store.accept({'record':duplicate}));empty=dict(r,source='https://example.org/empty',prompt='');self.assertFalse(self.store.accept({'record':empty}));self.assertEqual(self.store.snapshot()['readyCount'],1)
 def test_concurrent_use_has_no_double_consumption_or_overfill(self):
  self.fill();ids=[r['id'] for r in self.store.snapshot()['ready'][:5]]
  with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:results=list(pool.map(self.store.consume,ids+ids))
  self.assertEqual(sum(not r['alreadyUsed'] for r in results),5);self.assertEqual(self.store.snapshot()['readyCount'],50);self.assertEqual(self.store.snapshot()['usedCount'],5);self.assertEqual(self.store.snapshot()['reserveCount'],0)
 def test_export_snapshot_does_not_consume(self):
  self.fill();before=self.store.snapshot(True);json.dumps(self.store.snapshot(True));self.assertEqual(self.store.snapshot(True),before)
 def test_manual_edit_and_raw_data_survive_restart(self):
  self.fill();r=self.store.snapshot()['ready'][0];raw=r['raw'];edited=self.store.edit(r['id'],{'age':'33','prompt':'Edited roommate definition with mature characters and a quiet conversational style. '*5});self.assertEqual(edited['fieldSources']['prompt'],'手动编辑');self.store.close();restored=PoolStore(self.temp.name,self.client);self.store=restored;saved=restored.get_record(r['id']);self.assertEqual(saved['age'],'33');self.assertEqual(saved['raw'],raw)
 def test_no_reserve_reports_real_shortage_instead_of_placeholder(self):
  self.store.accept(self.client.read({'url':'https://example.org/one'}));old=self.store.snapshot()['ready'][0]['id'];result=self.store.consume(old);self.assertIsNone(result['replacementId']);self.assertEqual(self.store.snapshot()['readyCount'],0);self.assertEqual(self.store.snapshot()['usedCount'],1)
 def test_corrupt_storage_is_not_overwritten(self):
  path=Path(self.temp.name)/'pool.json';path.write_text('{broken',encoding='utf-8')
  self.store.close()
  with self.assertRaises(ValueError):PoolStore(self.temp.name,self.client)
  self.assertEqual(path.read_text(encoding='utf-8'),'{broken')

class MappingTests(unittest.TestCase):
 def test_source_definitions_are_preserved_without_demographic_guesses(self):
  response={'node':{'fullPath':'author/role','name':'Role','is_public':True,'topics':['Female'],'avatar_url':'https://example.org/a.png','definition':{'name':'Role','personality':'Age: 28\nGender: Non-binary\nOriginal setting.','tavern_personality':'Patient','scenario':'A station','first_message':'Welcome','description':'Creator notes','example_dialogs':'Example','embedded_lorebook':{'entries':[{'content':'Original lore'}]}}}}
  r=chub_record(response,'https://chub.ai/characters/author/role','endpoint');self.assertEqual(r['gender'],'Non-binary');self.assertEqual(r['age'],'28');self.assertEqual(r['intro'],'Creator notes');self.assertIn('Original setting.',r['prompt']);self.assertEqual(r['raw']['apiResponse'],response)
if __name__=='__main__':unittest.main()
