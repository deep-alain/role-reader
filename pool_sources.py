"""Public card catalogue adapters used by the persistent 50-bot shelf."""
import base64,hashlib,json,re,struct,urllib.parse,zlib
from datetime import datetime,timezone
from pool_policy import QUERIES
from source_metrics import metadata,rank_items

FIELDS={'category':'角色类别','name':'角色名称','avatar':'角色头像','gender':'性别','age':'角色年龄','scenario':'Scenario','greet':'角色greet','intro':'角色介绍','prompt':'角色prompt'}
SOURCES=('chub','botbooru','charavault')
def now_iso():return datetime.now(timezone.utc).isoformat()
def text(value):
 if isinstance(value,str):return value.strip()
 if isinstance(value,(int,float)) and not isinstance(value,bool):return str(value)
 if isinstance(value,list):return ', '.join(filter(None,(text(v.get('name',v.get('label',''))) if isinstance(v,dict) else text(v) for v in value)))
 return ''
def gender(value):
 value=re.sub(r'[_\s]+','-',text(value).lower())
 if value in ('male','man','boy','m','男性','男','男生','男朋友'):return 'Male'
 if value in ('female','woman','girl','f','女性','女','女生','女朋友'):return 'Female'
 if value in ('non-binary','nonbinary','nb','enby','非二元','非二元性别'):return 'Non-binary'
 return ''
def normalize_record(record):
 r=dict(record)
 for key in FIELDS:r[key]=text(r.get(key))
 r['gender']=gender(r['gender']);r['error']=text(r.get('error'));r['missing']=[label for key,label in FIELDS.items() if not r[key]]
 r['status']='failed' if r['error'] else 'partial' if r['missing'] else 'complete'
 return r
def card_to_record(card,source,avatar='',method='公开角色卡'):
 if not isinstance(card,dict):raise ValueError('Invalid character card')
 d=card.get('data',card.get('character',card));ext=d.get('extensions') or {};meta=d.get('metadata') or {}
 if not isinstance(d,dict):raise ValueError('Invalid character-card data')
 ext=ext if isinstance(ext,dict) else {};meta=meta if isinstance(meta,dict) else {}
 description=d.get('description') or d.get('char_persona','')
 demos=text(description)+'\n'+text(d.get('personality'))
 age_match=re.search(r'(?:\bage\b|年龄|年齡)\s*[:：=\-(]\s*(\d{1,4})',demos,re.I) or re.search(r'\b(\d{1,4})\s*(?:years?[- ]old|y/?o)\b',demos,re.I) or re.search(r'(\d{1,4})\s*[岁歳]',demos)
 gender_match=re.search(r'(?:\bgender\b|性别|性別)\s*[:：=\-(]\s*(non[- ]?binary|female|male|非二元性别|非二元|女性|男性|女|男)(?=\s|[.,;，。；)\]]|$)',demos,re.I)
 parts=[('Description',description),('Personality',d.get('personality')),('System prompt',d.get('system_prompt')),('Post-history instructions',d.get('post_history_instructions')),('Example dialogue',d.get('mes_example') or d.get('example_dialogue'))]
 prompt='\n\n'.join('['+label+']\n'+text(value) for label,value in parts if text(value)) or text(d.get('prompt') or d.get('角色prompt'))
 r={'id':hashlib.sha256(source.encode()).hexdigest()[:24],'category':text(d.get('category') or d.get('tags') or d.get('角色类别')),
 'name':text(d.get('name') or d.get('char_name') or d.get('character_name') or d.get('角色名称')),
 'avatar':avatar or text(d.get('avatar') or d.get('avatar_url') or d.get('image_url')),
 'gender':gender(d.get('gender') or ext.get('gender') or meta.get('gender')) or (gender(gender_match[1]) if gender_match else ''),
 'age':text(d.get('age') or ext.get('age') or meta.get('age')) or (age_match[1] if age_match else ''),
 'scenario':text(d.get('scenario') or d.get('world_scenario') or d.get('Scenario')),
 'greet':text(d.get('first_mes') or d.get('first_message') or d.get('initial_message') or d.get('char_greeting') or d.get('greeting')),
 'intro':text(d.get('creator_notes') or description),'prompt':prompt,'creator':text(d.get('creator')),
 'source':source,'method':method,'readAt':now_iso(),'error':'','notes':'作者未公开的字段留空；没有自动补写。','raw':{'card':card}}
 r['fieldSources']={key:method for key in FIELDS if r[key]}
 return normalize_record(r)
def png_card(data):
 if data[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('Download is not a PNG character card')
 found={};p=8
 while p+12<=len(data):
  n=struct.unpack_from('>I',data,p)[0];kind=data[p+4:p+8]
  if n>30*1024*1024 or p+n+12>len(data):raise ValueError('Corrupt PNG chunk')
  value=data[p+8:p+8+n]
  if kind==b'tEXt' and b'\0' in value:
   key,raw=value.split(b'\0',1)
   if key in (b'chara',b'ccv3'):found[key]=raw
  elif kind==b'iTXt' and b'\0' in value:
   key,tail=value.split(b'\0',1)
   if key in (b'chara',b'ccv3') and len(tail)>2 and tail[0]==0:
    tail=tail[2:];_,_,tail=tail.partition(b'\0');_,_,tail=tail.partition(b'\0');found[key]=tail
  p+=n+12
  if kind==b'IEND':break
 raw=found.get(b'ccv3') or found.get(b'chara')
 if not raw:raise ValueError('No character-card metadata in PNG')
 try:return json.loads(raw)
 except (ValueError,UnicodeError):return json.loads(base64.b64decode(raw,validate=True))
def chub_record(response,source,endpoint):
 node=response.get('node') or {};d=node.get('definition')
 if not isinstance(d,dict):raise ValueError('Chub did not publish definition')
 if node.get('is_public') is False or d.get('is_public') is False:raise ValueError('Character definition is not public')
 card={'spec':'chara_card_v2','spec_version':'2.0','data':{'name':d.get('name') or node.get('name'),'description':d.get('personality',''),'personality':d.get('tavern_personality',''),'scenario':d.get('scenario',''),'first_mes':d.get('first_message',''),'mes_example':d.get('example_dialogs',''),'creator_notes':d.get('description') or node.get('description',''),'system_prompt':d.get('system_prompt',''),'post_history_instructions':d.get('post_history_instructions',''),'alternate_greetings':d.get('alternate_greetings') or [],'tags':node.get('topics') or [],'creator':text(node.get('fullPath') or d.get('full_path')).split('/')[0],'character_book':d.get('embedded_lorebook'),'extensions':d.get('extensions') or {}}}
 r=card_to_record(card,source,node.get('max_res_url') or node.get('avatar_url') or '','Chub 公开接口')
 r.update(metadata('chub',node));r['raw'].update(apiEndpoint=endpoint,apiResponse=response);return r

class PublicCatalogue:
 def __init__(self,fetcher):self.fetch=fetcher
 def read_metadata(self,record):
  source=record.get('catalogueSource') or record.get('metricsSource')
  if source=='chub':
   endpoint=(record.get('raw') or {}).get('apiEndpoint')
   if not endpoint:raise ValueError('来源详情地址缺失')
   node=json.loads(self.fetch(endpoint)[0]).get('node') or {}
  elif source=='botbooru':
   role_id=urllib.parse.urlsplit(record['source']).path.rstrip('/').split('/')[-1]
   if not re.fullmatch(r'\d{1,12}',role_id):raise ValueError('来源 ID 无效')
   value=json.loads(self.fetch('https://botbooru.com/post/'+role_id)[0]);node=value.get('post',value)
  elif source=='charavault':
   path=[urllib.parse.unquote(s) for s in urllib.parse.urlsplit(record['source']).path.split('/') if s]
   if len(path)!=3 or path[0]!='cards':raise ValueError('来源角色地址无效')
   endpoint='https://charavault.net/api/cards?'+urllib.parse.urlencode({'q':record['name'][:128],'limit':100,'offset':0,'sort':'most_downloaded','nsfw':'false'})
   rows=json.loads(self.fetch(endpoint)[0]).get('results') or []
   node=next((n for n in rows if n.get('folder')==path[1] and n.get('file')==path[2]),None)
   if node is None:raise ValueError('公开目录未匹配此角色，热度与分级保持未知')
  else:raise ValueError('未知来源')
  return metadata(source,node)
 def catalogue(self,source,page=1,limit=20):
  query=QUERIES[(page-1+SOURCES.index(source))%len(QUERIES)];cycle=(page-1)//len(QUERIES);query_page=cycle//2+1
  sort={'chub':('n_favorites','download_count'),'botbooru':('views','favorites'),'charavault':('most_downloaded','top_rated')}[source][cycle%2]
  if source=='chub':
   url='https://gateway.chub.ai/search?'+urllib.parse.urlencode({'search':query,'namespace':'characters','first':limit,'page':query_page,'sort':sort,'asc':'false','nsfw':'false','nsfl':'false','count':'true','include_forks':'true'})
   data=json.loads(self.fetch(url)[0]);nodes=data.get('data',{}).get('nodes');total=data.get('data',{}).get('count',0)
   if not isinstance(nodes,list):raise ValueError('Chub catalogue response is invalid')
   items=[{'source':source,'url':'https://chub.ai/characters/'+n['fullPath'],'endpoint':'https://api.chub.ai/api/characters/'+n['fullPath']+'?full=true'} for n in nodes if isinstance(n.get('fullPath'),str) and len(n['fullPath'].split('/'))==2]
  elif source=='botbooru':
   url='https://botbooru.com/posts/?'+urllib.parse.urlencode({'q':'','qtext':query,'limit':limit,'offset':(query_page-1)*limit,'sort':sort,'time_window':'all'})
   data=json.loads(self.fetch(url)[0]);nodes=data.get('posts');total=data.get('total',0)
   if not isinstance(nodes,list):raise ValueError('BotBooru catalogue response is invalid')
   items=[{'source':source,'url':f'https://botbooru.com/character/{n["id"]}','endpoint':f'https://botbooru.com/download/png/{n["id"]}'} for n in nodes if re.fullmatch(r'\d{1,12}',str(n.get('id','')))]
  elif source=='charavault':
   url='https://charavault.net/api/cards?'+urllib.parse.urlencode({'q':query,'limit':limit,'offset':(query_page-1)*limit,'sort':sort,'nsfw':'false'})
   data=json.loads(self.fetch(url)[0]);nodes=data.get('results');total=data.get('total',0)
   if not isinstance(nodes,list):raise ValueError('CharaVault catalogue response is invalid')
   items=[]
   for n in nodes:
    if not isinstance(n.get('folder'),str) or not isinstance(n.get('file'),str):continue
    path='/'.join(urllib.parse.quote(n[k],safe='') for k in ('folder','file'))
    items.append({'source':source,'url':'https://charavault.net/cards/'+path,'endpoint':'https://charavault.net/api/cards/download/'+path})
  else:raise ValueError('Unknown catalogue source')
  for item,node in zip(items,[n for n in nodes if (source!='chub' or isinstance(n.get('fullPath'),str) and len(n['fullPath'].split('/'))==2) and (source!='botbooru' or re.fullmatch(r'\d{1,12}',str(n.get('id','')))) and (source!='charavault' or isinstance(n.get('folder'),str) and isinstance(n.get('file'),str))]):
   item.update(metadata(source,node,sort=sort,query=query,page=query_page,position=(query_page-1)*limit+nodes.index(node)+1))
  metric={'n_favorites':'favorites','download_count':'downloads','views':'views','favorites':'favorites','top_rated':'rating'}.get(sort)
  if metric:items=rank_items(items,metric)
  return {'items':items,'nextPage':page+1 if page<1000 else 1}
 def read(self,item):
  data,content_type=self.fetch(item['endpoint']);image=None
  if item['source']=='chub':r=chub_record(json.loads(data),item['url'],item['endpoint'])
  else:
   r=card_to_record(png_card(data),item['url'],item['endpoint'],item['source']+' 角色卡下载接口');image=data
   r['raw'].update(apiEndpoint=item['endpoint'],downloadBytes=len(data))
  r['catalogueSource']=item['source']
  for key in ('sourceMetrics','sourceRating','metricsSource','metricsAt','sourceRanking'):
   if key in item:r[key]=item[key]
  return {'record':r,'image':image,'imageType':'image/png' if image else ''}
