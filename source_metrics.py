"""Keep source popularity and source content rating distinct from local filtering."""
import math
from datetime import datetime,timezone

def number(value):
 return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None

def metrics(source,node):
 fields={
  'chub':{'favorites':'n_favorites','downloads':'starCount','chats':'nChats','messages':'nMessages','rating':'rating','ratingCount':'ratingCount'},
  'botbooru':{'views':'views','downloads':'downloads','favorites':'favorite_count','comments':'comments_count'},
  'charavault':{'rating':'avg_rating','ratingCount':'rating_count','comments':'comment_count'},
 }
 return {key:number(node.get(field)) for key,field in fields[source].items()}

def source_rating(source,node):
 tags=node.get('topics' if source=='chub' else 'tags') or []
 names={str(t.get('name','') if isinstance(t,dict) else t).strip().casefold().removeprefix('meta:') for t in tags}
 if node.get('is_nsfl') is True or 'nsfl' in names:return 'NSFL'
 if node.get('is_nsfw') is True or node.get('nsfw') is True or 'nsfw' in names:return 'NSFW'
 if node.get('is_nsfw') is False or node.get('nsfw') is False or 'sfw' in names:return 'SFW'
 # Chub's official frontend classifies a character from the complete topics field.
 if source=='chub' and isinstance(node.get('topics'),list):return 'SFW'
 return 'unknown'

def metadata(source,node,**ranking):
 return {'sourceMetrics':metrics(source,node),'sourceRating':source_rating(source,node),'metricsSource':source,
  'metricsAt':datetime.now(timezone.utc).isoformat(),'sourceRanking':ranking}

def rank_items(items,metric):
 # Missing values sort last. Zero remains a real source value, never "missing".
 return sorted(items,key=lambda item:((item.get('sourceMetrics') or {}).get(metric) is not None,(item.get('sourceMetrics') or {}).get(metric) or 0),reverse=True)

CAPABILITIES={
 'chub':{'label':'Chub','catalogue':'https://gateway.chub.ai/search','detail':'https://api.chub.ai/api/characters/{creator}/{slug}?full=true','sorts':['n_favorites','download_count'],'metrics':['favorites','downloads','chats','messages'],'anonymousCatalogue':True,'nsfwAccess':'requires_login','note':'收藏、下载、聊天与消息数；下载不是点赞。返回结果按实际计数再次排序。'},
 'botbooru':{'label':'BotBooru','catalogue':'https://botbooru.com/posts/','detail':'https://botbooru.com/post/{id}','download':'https://botbooru.com/download/png/{id}','sorts':['views','favorites'],'metrics':['views','favorites','downloads'],'anonymousCatalogue':True,'nsfwAccess':'requires_login','note':'浏览与收藏榜使用 time_window=all；收藏不是点赞。'},
 'charavault':{'label':'CharaVault','catalogue':'https://charavault.net/api/cards','download':'https://charavault.net/api/cards/download/{folder}/{file}','sorts':['most_downloaded','top_rated'],'metrics':['rating','ratingCount','comments'],'anonymousCatalogue':True,'nsfwAccess':'requires_login','note':'支持站内下载榜，但本次目录未返回下载计数、浏览数或点赞数。'},
 'janitor':{'label':'JanitorAI','anonymousCatalogue':False,'nsfwAccess':'not_verified','note':'此前访问被浏览器安全策略拦截，未接入或尝试镜像绕行；截图 Mirror 指向站内 /characters 路径。'},
 'crushon':{'label':'CrushOn','catalogue':'https://crushon.ai/api/trpc/character.getCharactersByTag','anonymousCatalogue':True,'nsfwAccess':'public_metadata_verified','metrics':['messages','thumbsUpCount','likeCount','clickPv'],'note':'官方 rating=2 为 NSFW；公开目录已实测 200，完整 prompt 尚未验证。'},
 'spicychat':{'label':'SpicyChat','catalogue':'https://ts-lb.nd-api.com/collections/public_characters_alias/documents/search','anonymousCatalogue':True,'nsfwAccess':'public_metadata_verified','metrics':['messages','rating'],'note':'使用站方公开发布的只读搜索配置；NSFW + 非 NSFW 头像标记 + 人设公开筛选已验证；详情样本 HTTP 401，需授权。'},
}
