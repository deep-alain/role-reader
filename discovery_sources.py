"""Read-only public candidate discovery. Does not claim private definitions are available."""
import json,re,threading,time,urllib.parse,urllib.request,urllib.error
from pool_policy import MINOR_TAGS,FAMILY_TAGS
from source_metrics import number

UUID=re.compile(r'^[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}$')
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):return None

def get_json(url,headers=None):
 request=urllib.request.Request(url,headers={'User-Agent':'RoleReader/1.5','Accept':'application/json',**(headers or {})})
 try:
  with urllib.request.build_opener(NoRedirect()).open(request,timeout=25) as response:
   data=response.read(8*1024*1024+1)
   if len(data)>8*1024*1024:raise ValueError('公开目录响应超过 8 MB')
   return json.loads(data)
 except urllib.error.HTTPError as error:
  raise ValueError(f'来源返回 HTTP {error.code}；未尝试其他凭据或隐藏接口') from None

def suitable_candidate(row):
 tags=row.get('tags') or []
 names={str(t.get('name',t.get('label',t.get('slug',''))) if isinstance(t,dict) else t).strip().casefold() for t in tags}
 name=str(row.get('name','')).casefold()
 if names&(MINOR_TAGS|FAMILY_TAGS):return False
 if re.search(r'\b(?:underage|loli|shota|schoolgirl|schoolboy|little sister|little brother|daughter|stepdaughter|incest|high school|middle school)\b|未成年|萝莉|正太|幼女',name):return False
 age=number(row.get('age'))
 return age is None or age==0 or age>=18

class PublicDiscovery:
 def __init__(self,fetcher=get_json):
  self.fetch=fetcher;self.settings=None;self.settings_at=0;self.lock=threading.RLock()
 def spicy_settings(self):
  with self.lock:
   if self.settings is None or time.time()-self.settings_at>900:
    self.settings=self.fetch('https://prod.nd-api.com/v2/applications/spicychat');self.settings_at=time.time()
   return self.settings
 def search(self,source,query='roommate',sort='popular',nsfw_only=True,page=1):
  if source not in ('crushon','spicychat') or sort not in ('popular','likes'):raise ValueError('未知来源或排序方式')
  if not isinstance(query,str) or len(query)>128:raise ValueError('关键词最多 128 个字符')
  if not isinstance(nsfw_only,bool) or not isinstance(page,int) or not 1<=page<=100:raise ValueError('筛选参数无效')
  result=self.crushon(query,sort,nsfw_only,page) if source=='crushon' else self.spicy(query,sort,nsfw_only,page)
  return result
 def crushon(self,query,sort,nsfw_only,page):
  if page!=1:raise ValueError('CrushOn 当前接入首批热门结果，暂不伪造翻页游标')
  # Slugs come from the site's public character.getTags response. "popular" is
  # the official popularity collection; no invented query or hidden-definition route.
  values=self.fetch('https://crushon.ai/api/trpc/character.getTags')['result']['data']['json']
  matches=[t['slug'] for t in values if query and any(query.casefold() in str(t.get(k,'')).casefold() for k in ('slug','label'))][:8]
  params={'tags':matches,'collectionKind':'','sortTag':'popular','nsfw':True,'gender':0,'count':20,'locale':'en','flyingNsfw':False,'version':0}
  endpoint='https://crushon.ai/api/trpc/character.getCharactersByTag?'+urllib.parse.urlencode({'input':json.dumps({'json':params},separators=(',',':'))})
  data=self.fetch(endpoint)['result']['data']['json'];items=[]
  for row in data.get('characters',[]):
   if not UUID.fullmatch(str(row.get('id',''))) or not suitable_candidate(row):continue
   rating={1:'SFW',2:'NSFW',3:'NSFW'}.get(row.get('rating'),'unknown')
   if rating=='NSFL' or nsfw_only and rating!='NSFW':continue
   stat=row.get('metric') or {}
   items.append({'id':row['id'],'name':str(row.get('name','')),'url':'https://crushon.ai/character/'+row['id']+'/details','source':'crushon','sourceRating':rating,'sourceLevel':row.get('rating'),'age':number(row.get('age')),
    'metrics':{'messages':number(stat.get('message_count')),'likes':number(stat.get('thumbsUpCount')),'liked':number(stat.get('likeCount')),'clicks':number(stat.get('clickPv'))},
    'definitionAccess':'未验证完整人设；仅公开目录候选','avatarReview':'未检查，候选列表不展示原图','ageEvidence':'需详情确认' if not number(row.get('age')) else str(row['age'])})
  metric='likes' if sort=='likes' else 'messages';items.sort(key=lambda r:r['metrics'].get(metric) if r['metrics'].get(metric) is not None else -1,reverse=True)
  note='CrushOn 公开热门目录；原站 rating=2 已核实为 NSFW。完整 prompt 尚未验证。'
  if sort=='likes':note+=' 当前热门批次按点赞计数重排，并非全站点赞总榜。'
  if query and not matches:note+=' 当前关键词未匹配官方标签，已显示热门总榜。'
  return {'source':'crushon','items':items,'page':1,'nextPage':None,'found':None,'ratingVerified':True,'note':note,'endpoint':'https://crushon.ai/api/trpc/character.getCharactersByTag'}
 def spicy(self,query,sort,nsfw_only,page):
  settings=self.spicy_settings();config=settings.get('typesenseConfig') or {}
  collection=config.get('collectionNamePublicCharacter');key=config.get('apiKeyAllPublicCharacters')
  if collection!='public_characters_alias' or not isinstance(key,str) or not key:raise ValueError('公开搜索配置已变化，请重新核验；未尝试其他密钥')
  filters='visibility:public && avatar_is_nsfw:false && definition_visible:true'
  if nsfw_only:filters+=' && is_nsfw:true'
  params={'q':query or '*','query_by':'name,title,tags','filter_by':filters,'include_fields':'character_id,name,tags,num_messages,rating_score,is_nsfw,avatar_is_nsfw,definition_visible','sort_by':'rating_score:desc' if sort=='likes' else 'num_messages:desc','per_page':20,'page':page}
  endpoint='https://ts-lb.nd-api.com/collections/public_characters_alias/documents/search?'+urllib.parse.urlencode(params)
  data=self.fetch(endpoint,{'X-TYPESENSE-API-KEY':key});items=[]
  for hit in data.get('hits',[]):
   row=hit.get('document') or {};role_id=str(row.get('character_id',''))
   if not UUID.fullmatch(role_id) or not suitable_candidate(row) or row.get('avatar_is_nsfw') is not False or row.get('definition_visible') is not True:continue
   rating='NSFW' if row.get('is_nsfw') is True else 'SFW' if row.get('is_nsfw') is False else 'unknown'
   if nsfw_only and rating!='NSFW':continue
   items.append({'id':role_id,'name':str(row.get('name','')),'url':'https://spicychat.ai/chatbot/'+role_id,'source':'spicychat','sourceRating':rating,'metrics':{'messages':number(row.get('num_messages')),'rating':number(row.get('rating_score'))},
    'definitionAccess':'原站标为公开；详情接口本次返回 401，需授权','avatarReview':'原站标记非 NSFW；尚未图像检查','ageEvidence':'需详情确认'})
  found=number(data.get('found'));next_page=page+1 if found is not None and page*20<found and page<100 else None
  return {'source':'spicychat','items':items,'found':found,'page':page,'nextPage':next_page,'ratingVerified':True,'endpoint':'https://ts-lb.nd-api.com/collections/public_characters_alias/documents/search',
   'note':f'SpicyChat 公开 NSFW 搜索已验证；按{"消息数" if sort=="popular" else "评分"}排序。评分榜不是点赞榜。源站头像标记不等同于自动露点检测；完整人设样本读取返回 401，未作为完整 Bot 入库。'}
