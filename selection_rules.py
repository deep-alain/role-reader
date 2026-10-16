"""Explicit ranking rules. Source ratings and popularity counters are never invented."""
import copy,json,os,re,threading
from pathlib import Path
from source_metrics import number

DEFAULT_RULES={'version':1,'minimumViews':0,'minimumMessages':0,'minimumLikes':0,'verifiedAdultsFirst':True,'safeAvatarsOnly':True}
RULE_TEXT=['排除未成年、学校设定、年龄扮演与 NSFL；年龄未确认的候选只进入待核验列表。',
 '优先成熟魅力人像、真人 / 写实风格及你标记多来的角色；精细人物插画可保留，默认图标、非人像、过淡线稿、Q版卡通排除。新图检查通过前不计入待用库存。',
 '已确认成年优先；原站分级如实标注，不推测色情含量或百分比，也不据此提高排名。',
 '有真实浏览数时按浏览数排序；未提供浏览数时使用原站消息/互动榜，并单独标注热度依据。',
 '跨站使用本批各来源内部热度顺序，不把消息、点击、收藏或评分换算成浏览数。',
 '同档再按真实点赞/收藏排序；缺失计数保持未知，真实的 0 保留为 0。',
 '头像未经检查继续遮挡；读取和排序不会消耗库存，点击已使用才换新。']

class RuleStore:
 def __init__(self,directory):
  self.path=Path(directory)/'selection_rules.json';self.lock=threading.RLock();self.rules=dict(DEFAULT_RULES)
  if self.path.exists():self.rules.update(self.validate(json.loads(self.path.read_text(encoding='utf-8'))))
  else:self.save({})
 def validate(self,changes):
  if not isinstance(changes,dict) or set(changes)-set(DEFAULT_RULES):raise ValueError('未知排序规则')
  out={}
  for key,value in changes.items():
   if key in ('minimumViews','minimumMessages','minimumLikes'):
    if not isinstance(value,int) or isinstance(value,bool) or not 0<=value<=10**12:raise ValueError('热度门槛必须是 0 至 1 万亿的整数')
   elif key=='version':
    if type(value) is not int or value!=1:raise ValueError('未知规则版本')
   elif not isinstance(value,bool):raise ValueError('无效规则开关')
   if key in ('safeAvatarsOnly','verifiedAdultsFirst') and value is not True:raise ValueError('成年优先和安全头像要求保持启用')
   out[key]=value
  return out
 def snapshot(self):
  with self.lock:return {'rules':copy.deepcopy(self.rules),'explanation':list(RULE_TEXT)}
 def save(self,changes):
  changes=self.validate(changes)
  with self.lock:
   self.rules.update(changes);self.path.parent.mkdir(parents=True,exist_ok=True)
   tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.rules,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,self.path)
   return self.snapshot()

def adult_state(record):
 verification=record.get('verification') or {}
 if verification.get('ageStatus')=='blocked':return 'blocked'
 body='\n'.join(str(record.get(k) or '') for k in ('name','category','prompt','greet','scenario','intro'))
 if re.search(r'\b(?:underage|high school|middle school|schoolgirl|schoolboy|loli|shota|ageplay)\b|未成年|高中生|初中生|小学生',body,re.I):return 'blocked'
 age=number(record.get('age'))
 if age is None:
  raw=record.get('age','');age=int(raw) if isinstance(raw,str) and raw.isdigit() else None
 if age is not None and 0<age<18:return 'blocked'
 if verification.get('ageStatus')=='verified' or age is not None and age>=18 or record.get('adultEvidence') in ('明确年龄','作者成人标签'):return 'verified'
 return 'pending'

def heat(record):
 stat=record.get('metrics') or record.get('sourceMetrics') or {}
 for key in ('views','messages','chats','downloads','favorites','likes'):
  value=number(stat.get(key))
  if value is not None:return key,value
 return 'unknown',None

def rank_records(records,rules=None):
 rules={**DEFAULT_RULES,**(rules or {})};included=[]
 for original in records:
  r=copy.deepcopy(original);stat=r.get('metrics') or r.get('sourceMetrics') or {};age=adult_state(r)
  if age=='blocked' or r.get('sourceRating')=='NSFL':continue
  if any(rules[rule]>0 and (number(stat.get(metric)) is None or number(stat.get(metric))<rules[rule]) for rule,metric in [('minimumViews','views'),('minimumMessages','messages'),('minimumLikes','likes')]):continue
  r['ranking']={'ageStatus':age,'heatBasis':heat(r)[0],'heatValue':heat(r)[1],'completeFields':sum(bool(r.get(k)) for k in ('category','name','avatar','gender','age','scenario','greet','intro','prompt'))}
  included.append(r)
 groups={}
 for r in included:
  source=r.get('metricsSource') or r.get('catalogueSource') or r.get('source','unknown')
  if source.startswith(('https://','http://')):
   from urllib.parse import urlsplit
   source=urlsplit(source).hostname
  groups.setdefault((source,r['ranking']['heatBasis']),[]).append(r)
 for group in groups.values():
  group.sort(key=lambda r:(r['ranking']['heatValue'] is not None,r['ranking']['heatValue'] or 0,number((r.get('metrics') or r.get('sourceMetrics') or {}).get('likes')) or 0,number((r.get('metrics') or r.get('sourceMetrics') or {}).get('favorites')) or 0),reverse=True)
  # Source-local positions in this fetched batch, never a global exposure percentage.
  for i,r in enumerate(group):r['ranking'].update(sourcePosition=i+1,sourceBatchSize=len(group),sourceOrder=1-i/max(1,len(group)-1))
 def key(r):
  stat=r.get('metrics') or r.get('sourceMetrics') or {};ranking=r['ranking']
  return (ranking['ageStatus']=='verified',r.get('preferencePriority',0),r.get('userPreference')=='more',ranking['heatValue'] is not None,ranking['sourceOrder'],number(stat.get('likes')) if number(stat.get('likes')) is not None else -1,number(stat.get('favorites')) if number(stat.get('favorites')) is not None else -1,ranking['completeFields'])
 return sorted(included,key=key,reverse=True)
