"""Small, inspectable preference model. Votes are evidence, never source popularity."""
import hashlib,re
from collections import defaultdict
from duplicate_guard import canonical_url,normalized_definition
from pool_policy import THEMES

STYLE_NAMES={'portrait':'人物头像','photo':'真人／写实人像','detailed_illustration':'精细人物插画','rendered_portrait':'人物渲染','glamour_portrait':'成熟魅力人像','default_icon':'默认图标','no_person':'非人像','faint_sketch':'过淡线稿','chibi_cartoon':'Q版卡通','other_rejected':'已排除的画风'}
MEDIUM_NAMES={'photorealistic':'照片／拟真人','illustration':'插画','rendered':'渲染'}
TRAITS={'confident':'自信','gentle':'温柔','protective':'守护','shy':'害羞','slow burn':'慢热','tsundere':'傲娇','yandere':'占有欲'}
QUERY_MAP={'哥特与冷感反差':'goth','奇幻人外':'elf','室友与同居':'roommate','青梅与朋友转恋人':'childhood friend','恋人关系冲突':'girlfriend','职场与名流':'coworker','脆弱与守护':'roommate','占有欲与反差':'girlfriend'}

def features(record,portrait=None,choice=None):
 portrait=portrait or {};out=[]
 style=portrait.get('style');medium=portrait.get('medium') or ('photorealistic' if style=='photo' else None)
 if style in STYLE_NAMES:out.append('style:'+style)
 if medium in MEDIUM_NAMES:out.append('medium:'+medium)
 # A rejected icon/chibi picture is not evidence against its unrelated story genre.
 if choice=='less' and style in {'default_icon','no_person','faint_sketch','chibi_cartoon','other_rejected'}:return [f for f in out if f.startswith('style:')]
 themes=[t for t in record.get('toneMatches',[]) if t in THEMES]
 meta=' '.join(str(record.get(k) or '') for k in ('name','category'))
 if not themes:
  body=meta+' '+str(record.get('prompt') or '')[:20000]
  themes=[name for name,pattern in THEMES.items() if re.search(pattern,body,re.I)]
 out.extend('theme:'+t for t in themes)
 tags=meta.replace('_',' ').replace('-',' ')
 out.extend('trait:'+t for t in TRAITS if re.search(r'\b'+re.escape(t)+r'\b',tags,re.I))
 return sorted(set(out))

def identities(record):
 keys=['source:'+canonical_url(record.get('source',''))]
 asset=record.get('avatarAsset') or {};signature=asset.get('visualSignature') or {}
 if asset.get('sha256'):keys.append('image:'+asset['sha256'])
 if signature.get('pixelHash'):keys.append('pixels:'+signature['pixelHash'])
 for definition in {normalized_definition(record),normalized_definition(record,[record.get('name','')])}:
  if len(definition)>=200:keys.append('definition:'+hashlib.sha256(definition.encode()).hexdigest())
 return keys

def label(feature):
 kind,value=feature.split(':',1)
 return STYLE_NAMES.get(value,value) if kind=='style' else MEDIUM_NAMES.get(value,value) if kind=='medium' else TRAITS.get(value,value) if kind=='trait' else value

def matching_votes(feedback,record):
 """Resolve the same transitive identity cluster used when learning."""
 keys=set(identities(record));matched={};changed=True
 while changed:
  changed=False
  for source,vote in feedback.items():
   vote_keys=set(vote.get('identityKeys') or ['source:'+canonical_url(source)])
   if vote.get('sha256'):vote_keys.add('image:'+vote['sha256'])
   if source not in matched and keys.intersection(vote_keys):
    matched[source]=vote;keys.update(vote_keys);changed=True
 return matched

def vote_order(vote):return (vote.get('sequence',0),vote.get('at',''))

def build_model(feedback):
 # Connected identities count mirrored cards and repeated clicks as one example.
 rows=sorted(feedback.items(),key=lambda pair:vote_order(pair[1]))
 parent=list(range(len(rows)));owners={}
 def find(i):
  while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
  return i
 for i,(source,vote) in enumerate(rows):
  for key in vote.get('identityKeys') or ['source:'+canonical_url(source)]:
   if key in owners:parent[find(i)]=find(owners[key])
   owners[key]=i
 latest={}
 for i,(_,vote) in enumerate(rows):latest[find(i)]=vote
 stats=defaultdict(lambda:{'more':0,'less':0})
 learned=0
 for vote in latest.values():
  if not vote.get('features') or vote.get('choice') not in ('more','less'):continue
  learned+=1
  for feature in set(vote['features']):stats[feature][vote['choice']]+=1
 for feature,value in stats.items():
  positive,negative=value['more'],value['less'];support=positive+negative
  value.update(feature=feature,label=label(feature),support=support,
               score=0 if negative>positive and negative<2 else round((positive-negative)/(support+3),4))
 queries=[]
 for feature,value in sorted(stats.items(),key=lambda pair:pair[1]['score'],reverse=True):
  if value['more']<2 or value['score']<=0:continue
  query=QUERY_MAP.get(feature.removeprefix('theme:')) if feature.startswith('theme:') else 'realistic girlfriend' if feature=='medium:photorealistic' else 'model girlfriend' if feature=='style:glamour_portrait' else None
  if query and query not in queries:queries.append(query)
 return {'version':1,'uniqueExamples':learned,'stats':dict(stats),'queries':queries[:5]}

def affinity(feature_list,model):
 matches=[model['stats'][f] for f in feature_list if f in model['stats'] and model['stats'][f]['score']]
 groups=defaultdict(list)
 for value in matches:
  kind=value['feature'].split(':',1)[0];groups['visual' if kind in ('style','medium') else kind].append(value['score'])
 weights={'visual':.8,'theme':1.,'trait':.5}
 score=round(100*sum(sum(values)/len(values)*weights[k] for k,values in groups.items())/sum(weights.values())) if groups else 0
 evidence=sorted(matches,key=lambda value:abs(value['score'])*weights['visual' if value['feature'].startswith(('style:','medium:')) else value['feature'].split(':',1)[0]],reverse=True)[:3]
 return {'version':1,'score':score,'band':1 if score>=10 else -1 if score<=-10 else 0,'evidence':evidence}

def model_summary(model):
 values=list(model['stats'].values())
 return {'version':1,'uniqueExamples':model['uniqueExamples'],'preferredFeatures':sorted((v for v in values if v['score']>0),key=lambda v:v['score'],reverse=True)[:5],
         'downrankedFeatures':sorted((v for v in values if v['score']<0),key=lambda v:v['score'])[:5],
         'discoveryQueries':model['queries'],'explanation':'同源、同图或相同人设合并计票；一次不要排除本条，两次独立反例后相似特征才降权。每四次目录读取保留一次原有轮换，避免只推荐一种题材。'}
