"""Selection profile derived from the user's reference topics; no text generation."""
import re,base64,html,json
from pool_avatars import display_avatar

PROFILE_ID='adult-story-romance-covered-v1'
PROFILE_LABEL='成年 · 故事型恋人 · 来源头像'
QUERIES=('girlfriend','boyfriend','goth','roommate','elf','childhood friend','boyfriend','coworker','demon','model girlfriend','childhood friend','girlfriend')
THEMES={
 '哥特与冷感反差':r'\bgoth(?:ic)?\b|cold[- ]hearted|哥特|冷感',
 '奇幻人外':r'\belf\b|\belven\b|\bkitsune\b|\bfoxgirl\b|\bdemon\b|\bsuccubus\b|\bandroid\b|精灵|狐娘|狐狸|恶魔|仿生人',
 '室友与同居':r'\broommate\b|\broom-mate\b|cohabit|室友|合租|同居',
 '青梅与朋友转恋人':r'childhood friend|best friend|friends.to.lovers|青梅竹马|儿时玩伴|朋友变恋人',
 '恋人关系冲突':r'\bgirlfriend\b|\bboyfriend\b|ex.girlfriend|ex.boyfriend|open relationship|reconcil|break.?up|jealous|女友|男友|恋人|前任|复合|吃醋',
 '职场与名流':r'\bco.worker\b|\bcoworker\b|\bcolleague\b|fashion model|supermodel|runway|同事|职场|模特|名模',
 '脆弱与守护':r'insomnia|sleepless|\bprotective\b|\bbodyguard\b|\bmafia\b|\bgangster\b|失眠|保镖|守护|帮派|黑帮',
 '占有欲与反差':r'\byandere\b|\btsundere\b|possessive|slow.?burn|secret crush|病娇|傲娇|占有欲|暗恋|慢热',
}
MINOR_TAGS={'minor','minors','underage','child','children','kid','kids','loli','shota','schoolgirl','schoolboy','teen','teenager','teenagers','child-like figure','childlike','幼女','萝莉','正太','未成年','小学生','初中生','高中生'}
FAMILY_TAGS={'incest','stepcest','family','mother','mom','sister','daughter','stepdaughter','stepsister','stepmother','父女','母子','兄妹','姐弟','妹妹','女儿'}
EXPLICIT=re.compile(r'\b(?:porn(?:ographic|ography)?|penetrat(?:e|es|ed|ing|ion)|orgasms?|ejaculat\w*|genitals?|vaginas?|penis|penises|cocks?|dicks?|puss(?:y|ies)|blowjobs?|handjobs?|masturbat\w*|cum(?:ming|shot)?|erotic roleplay|sexual roleplay)\b|性交|性交描写|生殖器|射精|阴茎|阴道',re.I)
def tag_set(record):
 return {re.sub(r'[_\s]+',' ',t.strip().casefold()) for t in re.split(r'[,，|;]',record.get('category','')) if t.strip()}
def evaluate(record):
 r=dict(record);r['fieldSources']=dict(r.get('fieldSources') or {});tags=tag_set(r)
 if r.get('sourceRating')=='NSFL':return False,'不纳入 NSFL 分级角色',r
 meta=' '.join((r.get('name',''),r.get('category',''))).casefold()
 body='\n'.join(str(r.get(k,'') or '') for k in ('prompt','greet','scenario','intro'))
 raw_card=(r.get('raw') or {}).get('card') or {};data=raw_card.get('data',raw_card) if isinstance(raw_card,dict) else {}
 extras=json.dumps({key:data.get(key) for key in ('alternate_greetings','character_book','extensions')},ensure_ascii=False) if isinstance(data,dict) else ''
 if re.search(r'\b(?:rpg|narrator|simulator|character card builder|character generator|character creator|prompt builder|assistant)\b|模拟器|生成器|提示词助手',meta,re.I):return False,'泛 RPG 或工具型角色',r
 if tags&(MINOR_TAGS|FAMILY_TAGS) or re.search(r'\b(?:underage|loli|shota|child[- ]like|high school|middle school)\b|未成年|小学生|初中生|高中生',meta+'\n'+body,re.I):return False,'年龄或关系设定不符合成年单角色筛选',r
 if EXPLICIT.search(body+'\n'+extras):return False,'不纳入露骨色情设定',r
 age=str(r.get('age','')).strip().replace(',','');age_value=int(age) if age.isdigit() and age.lstrip('0') else None
 # Include quoted labels and hyphenated ages, without inventing an age from appearance.
 captures=re.findall(r'(?:\bage\b|年龄|年齡)\s*[:：=\-("\s]+["\s]*(\d[\d,]{0,10})',body,re.I)
 captures+=re.findall(r'(?<![\d,.])(\d{1,3})[- ]years?[- ]old',body,re.I)
 explicit_ages=[int(v.replace(',','')) for v in captures]
 if any(v<18 for v in explicit_ages) or age_value is not None and age_value<18:return False,'发现未成年年龄设定',r
 if age_value is None and explicit_ages:age_value=explicit_ages[0];r['age']=str(age_value);r.setdefault('fieldSources',{})['age']='作者年龄标签'
 if age_value is None and not tags&{'adult','18+','adults','成年人'}:return False,'未找到明确的成年年龄或作者成人标签',r
 themes=[name for name,pattern in THEMES.items() if re.search(pattern,meta+'\n'+body,re.I)]
 if not themes:return False,'未匹配参考选题调性',r
 if len(r.get('prompt',''))<200 or len(r.get('greet',''))<60:return False,'人设或剧情开场过短',r
 genders={v for key,v in [('female','Female'),('male','Male'),('non-binary','Non-binary'),('non binary','Non-binary')] if key in tags}
 if not r.get('gender') and len(genders)==1:r['gender']=genders.pop();r.setdefault('fieldSources',{})['gender']='作者角色标签'
 original_avatar=r.get('avatarSource') or r.get('avatar','')
 initial=html.escape(r.get('name','?')[:1]);svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="400" viewBox="0 0 320 400"><rect width="320" height="400" fill="#493344"/><rect x="20" y="20" width="280" height="360" rx="14" fill="#65495a"/><text x="160" y="200" text-anchor="middle" fill="#f4e7dc" font-family="Arial,sans-serif" font-size="80">{initial}</text><text x="160" y="300" text-anchor="middle" fill="#dcc7cd" font-family="Arial,sans-serif" font-size="16">18+ · COVER</text></svg>'
 r['avatarSource']=original_avatar;r['avatar']='data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode()
 r['fieldSources']['avatar']='安全封面（来源原图未核验）'
 r['selectionProfile']=PROFILE_ID;r['toneMatches']=themes;r['avatarVisibility']='covered';r['contentRating']='成人向·非露骨';r['adultEvidence']='明确年龄' if age_value is not None else '作者成人标签'
 return True,'匹配成年故事型恋人选题',display_avatar(r)
