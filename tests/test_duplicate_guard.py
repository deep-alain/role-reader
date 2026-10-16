import tempfile,unittest
from io import BytesIO
from PIL import Image,ImageDraw,PngImagePlugin
from duplicate_guard import image_signature,same_portrait,duplicate_reason
from pool_store import PoolStore
from test_pool_store import FakeCatalogue

def portrait(metadata='',format='PNG',flip=False):
 image=Image.new('RGB',(96,128),'#ddd4cc');draw=ImageDraw.Draw(image)
 draw.ellipse((23,10,72,64),fill='#665044');draw.rectangle((15,65,81,127),fill='#295b69');draw.rectangle((33 if not flip else 53,28,39 if not flip else 60,34),fill='white')
 if flip:draw.rectangle((0,0,20,127),fill='red')
 out=BytesIO();info=PngImagePlugin.PngInfo();info.add_text('chara',metadata)
 image.save(out,format=format,**({'pnginfo':info} if format=='PNG' else {'quality':94}));return out.getvalue()

class DuplicateTests(unittest.TestCase):
 def test_same_pixels_with_different_card_metadata_are_duplicates(self):
  a=portrait('first bot name');b=portrait('changed bot name and prompt')
  self.assertNotEqual(a,b);self.assertTrue(same_portrait(image_signature(a),image_signature(b)))
 def test_light_recompression_is_detected_without_matching_a_different_image(self):
  self.assertTrue(same_portrait(image_signature(portrait()),image_signature(portrait(format='JPEG'))))
  self.assertFalse(same_portrait(image_signature(portrait()),image_signature(portrait(flip=True))))
 def test_renamed_definitions_deduplicate_but_short_generic_text_does_not(self):
  a={'source':'https://one.example/a','name':'Alpha','prompt':('Alpha is a patient adult clockmaker with a coastal workshop. '*25),'greet':'Alpha offers a seat.'}
  b={'source':'https://two.example/b','name':'Beta','prompt':a['prompt'].replace('Alpha','Beta'),'greet':'Beta offers a seat.'}
  self.assertIn('相同人设',duplicate_reason(a,b));self.assertIsNone(duplicate_reason({'prompt':'Hello'},{'prompt':'Hello'}))
 def test_inventory_sweep_retains_one_better_record_without_consumption(self):
  with tempfile.TemporaryDirectory() as folder:
   client=FakeCatalogue();pool=PoolStore(folder,client)
   one=client.read({'url':'https://one.example/a'});one['image']=portrait('one');one['record']['sourceMetrics']={'messages':100}
   two=client.read({'url':'https://two.example/b'});two['image']=portrait('two');two['record']['sourceMetrics']={'messages':500}
   pool.accept(one)
   # Simulate a legacy duplicate that predates pixel-aware ingestion.
   from pool_sources import normalize_record
   from pool_policy import evaluate
   from pool_avatars import cache_avatar
   import hashlib
   r=normalize_record(evaluate(two['record'])[2]);r['id']=hashlib.sha256(r['source'].encode()).hexdigest()[:24];r['avatarAsset']=cache_avatar(folder,r,two['image']);pool.state['ready'].append(r)
   pool.dedupe_inventory();self.assertEqual(pool.snapshot()['readyCount'],1);self.assertEqual(pool.snapshot()['usedCount'],0);self.assertEqual(pool.snapshot()['ready'][0]['sourceMetrics']['messages'],500);self.assertTrue(pool.state['retired'][-1]['duplicateOf'])

if __name__=='__main__':unittest.main()
