"""Deduplicate decoded portraits and near-identical public character definitions."""
import hashlib,re,unicodedata
from functools import lru_cache
from io import BytesIO
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from PIL import Image,ImageOps

SIGNATURE_VERSION=2

def _describe(image):
 small=image.resize((64,64),Image.Resampling.LANCZOS)
 gray=image.convert('L').resize((17,16),Image.Resampling.LANCZOS);pixels=gray.tobytes();bits=0
 for y in range(16):
  for x in range(16):bits=(bits<<1)|int(pixels[y*17+x]>pixels[y*17+x+1])
 return {'pixelHash':hashlib.sha256(small.tobytes()).hexdigest(),'differenceHash':f'{bits:064x}','colors':list(image.resize((4,4),Image.Resampling.BILINEAR).tobytes()),'aspect':round(image.width/image.height,5)}

def image_signature(data):
 with Image.open(BytesIO(data)) as original:
  if original.width*original.height>40_000_000:raise ValueError('头像像素超过 4000 万')
  image=ImageOps.exif_transpose(original).convert('RGBA')
  background=Image.new('RGBA',image.size,'white');background.alpha_composite(image);image=background.convert('RGB')
  image.thumbnail((640,640),Image.Resampling.LANCZOS)
  # Thumbnail services commonly center-crop full portraits to square avatars.
  # Keep substantial crop windows only; never compare two arbitrary face fragments.
  base=_describe(image);crops=[];seen={base['pixelHash']};area=image.width*image.height
  for aspect in (1,3/4,4/3):
   width=min(image.width,int(image.height*aspect));height=min(image.height,int(image.width/aspect))
   if width*height<area*.55:continue
   for position in (0,.25,.5,.75,1):
    x=round((image.width-width)*position);y=round((image.height-height)*position)
    candidate=_describe(image.crop((x,y,x+width,y+height)))
    if candidate['pixelHash'] not in seen:crops.append(candidate);seen.add(candidate['pixelHash'])
  return {**base,'version':SIGNATURE_VERSION,'crops':crops}

def _same_descriptor(a,b):
 if not a or not b:return False
 if a.get('pixelHash') and a.get('pixelHash')==b.get('pixelHash'):return True
 try:
  distance=(int(a['differenceHash'],16)^int(b['differenceHash'],16)).bit_count()
  colors=a['colors'];other=b['colors']
  return distance<=6 and len(colors)==len(other)==48 and sum(abs(x-y) for x,y in zip(colors,other))/48<=8
 except (KeyError,ValueError,TypeError):return False

def same_portrait(a,b):
 if _same_descriptor(a,b):return True
 if not a or not b:return False
 for full,other in ((a,b),(b,a)):
  for crop in other.get('crops',[]):
   aspect=full.get('aspect');crop_aspect=crop.get('aspect')
   if aspect and crop_aspect and abs(aspect/crop_aspect-1)<=.03 and _same_descriptor(full,crop):return True
 return False

def canonical_url(url):
 parts=urlsplit(url);query=urlencode([(k,v) for k,v in parse_qsl(parts.query) if not re.match(r'utm_|^(?:ref|from|referrer)$',k,re.I)])
 return urlunsplit((parts.scheme.casefold(),parts.netloc.casefold(),parts.path.rstrip('/'),query,''))

@lru_cache(maxsize=256)
def _normal_text(body):
 body=unicodedata.normalize('NFKC',body).casefold()
 body=re.sub(r'\[(?:description|personality|example dialogue|system prompt)\]',' ',body)
 return re.sub(r'\W+',' ',body).strip()

@lru_cache(maxsize=256)
def _words(body):return frozenset(body.split())

def normalized_definition(record,names=None):
 body='\n'.join(str(record.get(k) or '') for k in ('prompt','greet'))
 # A short name such as Ren must not change words such as "parents".
 # Comparing both records with the same aliases also catches renamed mirror cards.
 for name in sorted(set(names or []),key=len,reverse=True):
  if len(name)>=3:body=re.sub(r'(?<!\w)'+re.escape(name)+r'(?!\w)',' ROLE ',body,flags=re.I)
 return _normal_text(body)

def duplicate_reason(a,b):
 if canonical_url(a.get('source',''))==canonical_url(b.get('source','')) and a.get('source'):return '同一来源链接'
 aa=a.get('avatarAsset') or {};bb=b.get('avatarAsset') or {}
 if aa.get('sha256') and aa.get('sha256')==bb.get('sha256'):return '相同头像文件'
 if same_portrait(aa.get('visualSignature'),bb.get('visualSignature')):return '同一人物头像（含常见缩略图裁切 / PNG元数据 / 轻微压缩差异）'
 left=normalized_definition(a);right=normalized_definition(b)
 if min(len(left),len(right))<200:return None
 if left==right:return '相同人设和开场（忽略名称与排版）'
 vocab_left=_words(left);vocab_right=_words(right)
 if len(vocab_left&vocab_right)/max(1,len(vocab_left|vocab_right))<.65:return None
 aliases=[str(r.get('name') or '').strip() for r in (a,b)]
 left=normalized_definition(a,aliases);right=normalized_definition(b,aliases)
 if left==right:return '相同人设和开场（忽略名称与排版）'
 # Five-word shingles remain robust to small attribution/name edits.
 def shingles(value):
  words=value.split()[:5000]
  return {tuple(words[i:i+5]) for i in range(len(words)-4)}
 x=shingles(left);y=shingles(right)
 if x and y and len(x&y)/len(x|y)>=0.93:return '高度重复的人设与开场'
 return None
