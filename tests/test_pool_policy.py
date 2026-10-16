import unittest
from pool_policy import evaluate,PROFILE_ID

def role(**updates):
 r={'name':'Goth Roommate','category':'Female, Adult, Goth, Roommate','age':'26','gender':'','prompt':'Age: 26\n'+('She is a thoughtful goth roommate, an artist with a quiet personality and a dry sense of humor. '*4),'greet':'The rain taps at the windows. Your roommate looks up from her sketchbook and asks whether you want to share some tea.','scenario':'A shared apartment at night.','intro':'An adult romance with a slow start.','avatar':'https://example.org/original.png','fieldSources':{}}
 r.update(updates);return r
class PolicyTests(unittest.TestCase):
 def test_matches_reference_tone_and_retains_original_definition(self):
  original=role();ok,_,r=evaluate(original);self.assertTrue(ok);self.assertEqual(r['selectionProfile'],PROFILE_ID);self.assertIn('哥特与冷感反差',r['toneMatches']);self.assertEqual(r['prompt'],original['prompt']);self.assertEqual(r['gender'],'Female')
 def test_uncertain_avatar_is_replaced_by_non_nude_safe_cover(self):
  ok,_,r=evaluate(role());self.assertTrue(ok);self.assertTrue(r['avatar'].startswith('data:image/svg+xml;base64,'));self.assertEqual(r['avatarSource'],'https://example.org/original.png');self.assertEqual(r['avatarVisibility'],'covered')
 def test_minor_and_unclear_age_are_rejected(self):
  self.assertFalse(evaluate(role(age='16',prompt='Age: 16\n'+role()['prompt']))[0]);self.assertFalse(evaluate(role(age='',category='Female, Goth',prompt='A quiet roommate. '*20))[0]);self.assertFalse(evaluate(role(category='Adult, Female, Schoolgirl'))[0])
 def test_explicit_content_and_generic_rpg_are_rejected(self):
  self.assertFalse(evaluate(role(prompt=role()['prompt']+' Explicit sexual roleplay.'))[0]);self.assertFalse(evaluate(role(name='Fantasy RPG Narrator'))[0])
 def test_old_age_or_category_values_are_not_modified_in_place(self):
  original=role();evaluate(original);self.assertNotIn('gender',original['fieldSources']);self.assertEqual(original['avatar'],'https://example.org/original.png')
 def test_alternate_greetings_are_checked_as_well_as_main_fields(self):
  self.assertFalse(evaluate(role(raw={'card':{'data':{'alternate_greetings':['Explicit sexual roleplay.']}}}))[0])
if __name__=='__main__':unittest.main()
