"""Fetch source avatars for display in the shelf. Never follows arbitrary redirects."""
import hashlib,re,urllib.parse,urllib.request
from pathlib import Path

LIMIT=15*1024*1024
HOST_PATHS={
 'avatars.charhub.io':r'/avatars/.+',
 'avatars.chub.ai':r'/avatars/.+',
 'charavault.net':r'/api/cards/download/[^/]+/[^/]+',
 'botbooru.com':r'/download/png/\d{1,12}',
}
def validate_avatar_url(url):
 u=urllib.parse.urlsplit(url)
 if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or u.fragment or u.query:raise ValueError('Invalid avatar URL')
 if u.hostname not in HOST_PATHS or not re.fullmatch(HOST_PATHS[u.hostname],u.path):raise ValueError('Unconfigured avatar source')
 for part in u.path.split('/'):
  decoded=urllib.parse.unquote(part)
  if decoded in ('.','..') or '/' in decoded or '\\' in decoded or any(ord(c)<32 for c in decoded):raise ValueError('Invalid avatar path')
 return url

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):return None

def image_type(data):
 if len(data)>LIMIT:raise ValueError('Avatar exceeds 15 MB')
 if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png','png'
 if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg','jpg'
 if data[:4]==b'RIFF' and data[8:12]==b'WEBP':return 'image/webp','webp'
 raise ValueError('Avatar response is not PNG, JPEG or WebP')

def fetch_avatar(url):
 validate_avatar_url(url)
 req=urllib.request.Request(url,headers={'User-Agent':'RoleReader/1.4','Accept':'image/png,image/jpeg,image/webp'})
 with urllib.request.build_opener(NoRedirect()).open(req,timeout=25) as response:
  if int(response.headers.get('Content-Length','0'))>LIMIT:raise ValueError('Avatar exceeds 15 MB')
  data=response.read(LIMIT+1)
 image_type(data)
 return data

def fetch_record_avatar(record):
 primary=record['avatarSource'];alternatives=[primary]
 node=(record.get('raw') or {}).get('apiResponse',{}).get('node',{})
 if record.get('catalogueSource')=='chub' or record.get('source','').startswith('https://chub.ai/characters/'):
  if node.get('avatar_url') and node['avatar_url']!=primary:alternatives.append(node['avatar_url'])
 last_error=None
 for url in alternatives:
  try:return fetch_avatar(url),url
  except Exception as error:last_error=error
 raise last_error

def cache_avatar(directory,record,data):
 mime,ext=image_type(data);digest=hashlib.sha256(data).hexdigest()
 name=record['id']+'-'+digest[:16]+'.'+ext
 folder=Path(directory)/'avatars';folder.mkdir(exist_ok=True)
 (folder/name).write_bytes(data)
 old=record.get('avatarAsset') or {};reviewed=old.get('sha256')==digest and old.get('source')==record['avatarSource']
 from duplicate_guard import image_signature
 return {'file':name,'mime':mime,'sha256':digest,'source':record['avatarSource'],'bytes':len(data),'review':old.get('review','pending') if reviewed else 'pending','displayHidden':old.get('displayHidden',False) if reviewed else False,'visualSignature':image_signature(data)}

def display_avatar(record):
 asset=record.get('avatarAsset') or {}
 if asset.get('review')=='approved' and not asset.get('displayHidden') and asset.get('source')==record.get('avatarSource'):
  record['avatar']=record['avatarSource'];record['avatarVisibility']='source';record['fieldSources']['avatar']='来源真实头像（已检查可展示）'
 return record

def avatar_file(directory,record):
 asset=record.get('avatarAsset') or {};name=asset.get('file','')
 if not re.fullmatch(r'[a-f0-9]{24}-[a-f0-9]{16}\.(?:png|jpg|webp)',name):raise ValueError('Avatar has not been downloaded')
 path=Path(directory)/'avatars'/name
 data=path.read_bytes()
 if hashlib.sha256(data).hexdigest()!=asset.get('sha256'):raise ValueError('Avatar cache changed')
 return data,asset['mime']
