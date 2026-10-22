"""User feedback and source-bound portrait review. No inferred NSFW percentages."""
import copy,json,os,re,threading
from pathlib import Path
from datetime import datetime,timezone
from source_metrics import number
from preference_learning import features,identities,build_model,affinity,model_summary,matching_votes,vote_order,type_rule,blocked_types
from cover_preferences import CoverPreferences

ALLOWED_STYLES={'portrait','photo','detailed_illustration','rendered_portrait','glamour_portrait'}
REJECTED_STYLES={'default_icon','no_person','faint_sketch','chibi_cartoon','other_rejected'}
LIMITS={'views':80,'messages':100,'chats':30,'downloads':20,'favorites':10,'likes':10}
STYLE_LABELS={'portrait':'人物头像','photo':'真人 / 写实人像','detailed_illustration':'精细人物插画','rendered_portrait':'写实人物渲染','glamour_portrait':'成熟魅力人像','default_icon':'默认图标','no_person':'没有人物','faint_sketch':'过淡线稿','chibi_cartoon':'Q 版 / 简笔卡通','other_rejected':'不符合偏好'}

class TasteProfile:
 def __init__(self,directory,seed=None):
  self.path=Path(directory)/'taste_profile.json';self.lock=threading.RLock();self.state={'version':1,'feedback':{},'portraits':{},'targetRealisticPercent':0}
  if seed and Path(seed).exists():self.state.update(json.loads(Path(seed).read_text(encoding='utf-8')))
  if self.path.exists():
   saved=json.loads(self.path.read_text(encoding='utf-8'))
   for key in ('feedback','portraits'):self.state[key].update(saved.get(key,{}))
   self.state['targetRealisticPercent']=saved.get('targetRealisticPercent',0)
  self.covers=CoverPreferences(directory);self._model=None;self._save()
 def _save(self):
  self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,self.path)
 def model(self):
  with self.lock:
   if self._model is None:self._model=build_model(self.state['feedback'])
   return self._model
 def snapshot(self):
  with self.lock:return {'liked':sum(f['choice']=='more' for f in self.state['feedback'].values()),'rejected':sum(f['choice']=='less' for f in self.state['feedback'].values()),'learning':model_summary(self.model()),'cover':{**self.covers.snapshot(),'enabled':True},'targetRealisticPercent':self.state['targetRealisticPercent'],'heatFloors':dict(LIMITS),'styles':[STYLE_LABELS[s] for s in sorted(ALLOWED_STYLES)],'explanation':'“不要此类”会记住题材与人物特征组合，今后的读取、推荐和补货直接过滤同类；通用插画、人物头像、性别不作为整类禁令。可撤销。成年、不露点、人像核验和热度门槛保持优先。'}
 def hydrate_feedback(self,records):
  by_source={r.get('source'):r for r in records};changed=False
  with self.lock:
   for source,vote in self.state['feedback'].items():
    if 'features' in vote or source not in by_source:continue
    record=by_source[source];portrait=self.state['portraits'].get((record.get('avatarAsset') or {}).get('sha256'),{})
    vote.update(features=features(record,portrait,vote['choice']),identityKeys=identities(record));changed=True
   if changed:self._model=None;self._save()
   return changed
 def discovery_query(self,turn):
  queries=self.model()['queries']
  return queries[(turn//4*3+turn%4)%len(queries)] if queries and turn%4!=3 else None
 def set_realistic_target(self,percent):
  if type(percent) is not int or not 0<=percent<=100:raise ValueError('写实目标比例必须是 0 至 100 的整数')
  with self.lock:self.state['targetRealisticPercent']=percent;self._save()
 def realistic_target(self,total):
  with self.lock:return (total*self.state['targetRealisticPercent']+50)//100
 def is_realistic(self,record):
  with self.lock:portrait=self.state['portraits'].get((record.get('avatarAsset') or {}).get('sha256'),{})
  return portrait.get('medium')=='photorealistic' or portrait.get('medium') is None and portrait.get('style')=='photo'
 def feedback(self,record,choice):
  if choice not in ('more','less','clear'):raise ValueError('无效偏好')
  with self.lock:
   if choice=='clear':
    for source in matching_votes(self.state['feedback'],record):self.state['feedback'].pop(source,None)
    self._model=None;self._save();return
   portrait=self.state['portraits'].get((record.get('avatarAsset') or {}).get('sha256'),{})
   sequence=max((v.get('sequence',0) for v in self.state['feedback'].values()),default=0)+1
   vote={'choice':choice,'name':record['name'],'sha256':(record.get('avatarAsset') or {}).get('sha256'),'at':datetime.now(timezone.utc).isoformat(),'sequence':sequence,'features':features(record,portrait,choice),'identityKeys':identities(record)}
   if choice=='less':vote['typeRule']=type_rule(record,portrait)
   self.state['feedback'][record['source']]=vote;self._model=None;self._save()
 def review(self,record,style,medium=None):
  if style not in ALLOWED_STYLES|REJECTED_STYLES:raise ValueError('无效头像风格')
  asset=record.get('avatarAsset') or {};digest=asset.get('sha256')
  if not digest or asset.get('source')!=record.get('avatarSource'):raise ValueError('请先取得匹配的来源头像')
  if medium not in (None,'photorealistic','illustration','rendered'):raise ValueError('无效的人像媒介')
  if medium is None:medium={'photo':'photorealistic','detailed_illustration':'illustration','rendered_portrait':'rendered'}.get(style)
  with self.lock:
   previous=self.state['portraits'].get(digest,{})
   self.state['portraits'][digest]={'style':style,'at':datetime.now(timezone.utc).isoformat()}
   if medium or previous.get('medium'):self.state['portraits'][digest]['medium']=medium or previous['medium']
   for vote in self.state['feedback'].values():
    if vote.get('sha256')==digest and 'features' in vote:
     rest=[f for f in vote['features'] if not f.startswith(('style:','medium:'))]
     visual=features({},self.state['portraits'][digest],vote['choice'])
     vote['features']=sorted(set(visual+(rest if style in ALLOWED_STYLES else [])))
     if vote.get('choice')=='less' and 'typeRule' in vote:
      specific=[f for f in vote['typeRule']['features'] if not f.startswith('style:')]
      vote['typeRule']['features']=['style:'+style] if style in REJECTED_STYLES else specific
   self._model=None
   self._save()
 def evaluate(self,record):
  r=copy.deepcopy(record);r.pop('coverPreference',None);asset=r.get('avatarAsset') or {};digest=asset.get('sha256');source=r.get('source','')
  with self.lock:
   matches=matching_votes(self.state['feedback'],r)
   vote=copy.deepcopy(sorted(matches.values(),key=vote_order)[-1] if matches else {});portrait=copy.deepcopy(self.state['portraits'].get(digest) or {})
  if vote.get('choice')=='less':return 'reject','已按你的标注排除（同源、同图或相同人设）',r
  excluded=blocked_types(r,portrait,self.model())
  if excluded:
   r['excludedTypes']=[rule['key'] for rule in excluded]
   return 'reject','已按你的标注排除类型：'+'；'.join(rule['label'] for rule in excluded),r
  tags=' '.join(str(r.get(k) or '') for k in ('name','category')).replace('_',' ')
  if re.search(r'\b(?:fnf|friday night funkin|chibi|funko|pixel art)\b|Q版|像素风',tags,re.I):return 'reject','排除 Q 版、游戏简笔卡通',r
  style=portrait.get('style')
  if style in REJECTED_STYLES:return 'reject','排除头像：'+STYLE_LABELS[style],r
  stat=r.get('sourceMetrics') or {};basis=next((k for k in LIMITS if number(stat.get(k)) is not None),None)
  if basis and stat[basis]<LIMITS[basis] and vote.get('choice')!='more':return 'reject',f'热度不足：{basis} {stat[basis]} < {LIMITS[basis]}',r
  learned=affinity(features(r,portrait),self.model());r['preferenceLearning']=learned;r['userPreference']=vote.get('choice','')
  reason='；'.join(v['label'] for v in learned['evidence'][:2])
  r['learnedPreferenceReason']=('相似偏好加权：' if learned['score']>0 else '相似偏好降权：')+reason if learned['score'] else ''
  r['preferencePriority']=2 if style=='glamour_portrait' else 1 if style in ('photo','rendered_portrait') else 0
  # Previously unseen artwork needs a visible review; it never occupies a ready slot.
  if style not in ALLOWED_STYLES or asset.get('source')!=r.get('avatarSource') or asset.get('review')!='approved':
   r['preferenceReason']='头像待核验'+(' · '+r['learnedPreferenceReason'] if r['learnedPreferenceReason'] else '')
   return 'pending','人像风格 / 不露点待检查',r
  r['avatarTaste']={**portrait,'sha256':digest,'label':STYLE_LABELS[style]};r['userPreference']=vote.get('choice','')
  r['preferencePriority']=2 if style=='glamour_portrait' else 1 if style in ('photo','rendered_portrait') else 0
  r['preferenceReason']='你标记多来 · '+STYLE_LABELS[style] if vote.get('choice')=='more' else STYLE_LABELS[style]
  if self.is_realistic(r):r['preferenceReason']+=' · 真人／写实';r['portraitMedium']='photorealistic'
  else:r['portraitMedium']=portrait.get('medium','unknown')
  if not basis:r['preferenceReason']+=' · 热度未提供'
  if r['learnedPreferenceReason']:r['preferenceReason']+=' · '+r['learnedPreferenceReason']
  if self.covers.snapshot()['count']:
   from portrait_review import CLIP_EMBEDDING_VERSION
   embedding=self.covers.get_embedding(r,CLIP_EMBEDDING_VERSION)
   if embedding is not None:
    preference=self.covers.evaluate(r,embedding,CLIP_EMBEDDING_VERSION)
    if preference:r['coverPreference']=preference
  return 'accept','符合人像和热度偏好',r
