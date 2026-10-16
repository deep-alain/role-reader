import json,unittest,urllib.parse
from discovery_sources import PublicDiscovery,suitable_candidate

ROLE_ID='12345678-1234-1234-1234-123456789abc'
OTHER_ID='87654321-1234-1234-1234-123456789abc'
class DiscoveryTests(unittest.TestCase):
 def test_spicy_uses_published_read_only_search_config_and_preserves_real_flags(self):
  calls=[]
  def fetch(url,headers=None):
   calls.append((url,headers))
   if '/applications/' in url:return {'typesenseConfig':{'collectionNamePublicCharacter':'public_characters_alias','apiKeyAllPublicCharacters':'public-search-test-key'}}
   return {'found':1,'hits':[{'document':{'character_id':ROLE_ID,'name':'Adult roommate','tags':['Adult'],'is_nsfw':True,'avatar_is_nsfw':False,'definition_visible':True,'num_messages':1234,'rating_score':4.5}}]}
  result=PublicDiscovery(fetch).search('spicychat');self.assertEqual(result['items'][0]['metrics']['messages'],1234);self.assertNotIn('likes',result['items'][0]['metrics']);self.assertEqual(result['items'][0]['sourceRating'],'NSFW');self.assertNotIn('public-search-test-key',json.dumps(result))
  params=urllib.parse.parse_qs(urllib.parse.urlsplit(calls[1][0]).query);self.assertIn('is_nsfw:true',params['filter_by'][0]);self.assertIn('avatar_is_nsfw:false',params['filter_by'][0]);self.assertIn('definition_visible:true',params['filter_by'][0]);self.assertEqual(params['sort_by'],['num_messages:desc'])
 def test_mislabeled_spicy_results_do_not_pass_the_nsfw_filter(self):
  def fetch(url,headers=None):
   if '/applications/' in url:return {'typesenseConfig':{'collectionNamePublicCharacter':'public_characters_alias','apiKeyAllPublicCharacters':'public'}}
   return {'found':3,'hits':[{'document':{'character_id':ROLE_ID,'is_nsfw':False,'avatar_is_nsfw':False,'definition_visible':True}},{'document':{'character_id':OTHER_ID,'is_nsfw':True,'avatar_is_nsfw':True,'definition_visible':True}}]}
  self.assertEqual(PublicDiscovery(fetch).search('spicychat')['items'],[])
 def test_auth_error_propagates_without_fake_profiles_or_alternate_routes(self):
  calls=[]
  def fetch(url,headers=None):
   calls.append(url)
   if '/applications/' in url:return {'typesenseConfig':{'collectionNamePublicCharacter':'public_characters_alias','apiKeyAllPublicCharacters':'public'}}
   raise ValueError('HTTP 401')
  with self.assertRaisesRegex(ValueError,'401'):PublicDiscovery(fetch).search('spicychat')
  self.assertEqual(len(calls),2)
 def test_crushon_numeric_rating_is_verified_and_unknown_likes_are_not_zero(self):
  def fetch(url,headers=None):
   if 'getTags' in url:return {'result':{'data':{'json':[{'slug':'roommate','label':'Roommate'}]}}}
   return {'result':{'data':{'json':{'characters':[{'id':ROLE_ID,'name':'Adult roommate','rating':2,'age':26,'metric':{'message_count':99,'thumbsUpCount':0,'likeCount':7,'clickPv':0}},{'id':OTHER_ID,'name':'Other','rating':1,'metric':{'thumbsUpCount':999}}]}}}}
  result=PublicDiscovery(fetch).search('crushon');self.assertEqual(len(result['items']),1);self.assertEqual(result['items'][0]['sourceRating'],'NSFW');self.assertEqual(result['items'][0]['metrics']['likes'],0);self.assertNotIn('prompt',result['items'][0])
 def test_minor_age_or_minor_tags_never_qualify_as_candidates(self):
  self.assertFalse(suitable_candidate({'age':16,'name':'Roommate'}));self.assertFalse(suitable_candidate({'tags':['Loli']}));self.assertFalse(suitable_candidate({'name':'High school roommate'}));self.assertTrue(suitable_candidate({'age':28,'tags':['Adult']}))
 def test_no_arbitrary_source_or_fabricated_crushon_cursor(self):
  reader=PublicDiscovery(lambda *args: (_ for _ in ()).throw(AssertionError('must not fetch')))
  for kwargs in [{'source':'http://127.0.0.1'},{'source':'spicychat','page':0},{'source':'crushon','page':2},{'source':'crushon','query':'x'*129}]:
   with self.assertRaises(ValueError):reader.search(**kwargs)

if __name__=='__main__':unittest.main()
