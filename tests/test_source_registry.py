import json,tempfile,unittest
from pathlib import Path
from source_registry import SourceRegistry

class RegistryTests(unittest.TestCase):
 def test_config_can_disable_or_reorder_builtin_sources(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'sources.json';p.write_text(json.dumps({'builtinSources':['botbooru','chub']}));registry=SourceRegistry(lambda *a:None,p)
   self.assertEqual(registry.sources,('botbooru','chub'))
   with self.assertRaises(ValueError):registry.catalogue('charavault')
 def test_local_adapter_uses_the_same_catalogue_and_read_contract(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'sample.py').write_text('class Adapter:\n def catalogue(self,page,limit):return {"items":[{"url":"https://example.org/adult"}],"nextPage":None}\n def read(self,item):return {"record":{"name":"Sample","source":item["url"]}}\ndef create_adapter(options):return Adapter()\n')
   config=root/'sources.json';config.write_text(json.dumps({'builtinSources':[],'adapters':[{'id':'sample','module':'sample.py'}]}));registry=SourceRegistry(None,config)
   item=registry.catalogue('sample')['items'][0];self.assertEqual(item['source'],'sample');self.assertEqual(registry.read(item)['record']['name'],'Sample')
   with self.assertRaises(ValueError):registry.read_metadata({'catalogueSource':'sample'})
 def test_empty_duplicate_and_missing_adapter_configs_fail_explicitly(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'sources.json'
   for config in ({'builtinSources':[]},{'builtinSources':['chub','chub']},{'builtinSources':[],'adapters':[{'id':'missing','module':'missing.py'}]}):
    p.write_text(json.dumps(config))
    with self.assertRaises(ValueError):SourceRegistry(None,p)

if __name__=='__main__':unittest.main()
