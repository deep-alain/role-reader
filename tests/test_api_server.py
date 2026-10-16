import unittest
from api_server import validate_url

class EndpointValidation(unittest.TestCase):
 def test_allowed_public_endpoints(self):
  for url in ['https://api.chub.ai/api/characters/a/b?full=true','https://gateway.chub.ai/search?namespace=characters&nsfw=false','https://charavault.net/api/cards/download/creator/A%20card.png','https://botbooru.com/download/png/123']:
   self.assertEqual(validate_url(url),url)
 def test_rejects_unrelated_or_private_destinations(self):
  for url in ['http://127.0.0.1:1234/a','https://api.chub.ai.evil.example/api/characters/a/b','https://api.chub.ai/account','https://user:secret@botbooru.com/posts/','https://botbooru.com:8443/posts/','https://api.chub.ai/api/characters/a/%2e%2e','https://charavault.net/api/cards?token=secret']:
   with self.assertRaises(ValueError,msg=url):validate_url(url)
 def test_catalogue_is_anonymous_public_only(self):
  with self.assertRaises(ValueError):validate_url('https://gateway.chub.ai/search?nsfw=true')
if __name__=='__main__':unittest.main()
