"""Deduplicate decoded portraits and near-identical public character definitions."""
import hashlib,re,unicodedata
from io import BytesIO
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from PIL import Image,ImageOps

def image_signature(data):
 with Image.open(BytesIO(data)) as original:
  if original.width*original.height>40_000_000:raise ValueError('头像像素超过 4000 万')
  image=ImageOps.exif_transpose(original).convert('RGBA')
  background=Image.new('RGBA',image.size,'white');background.alpha_composite(image);image=background.convert('RGB')
  small=image.resize((64,64),Image.Resampling.LANCZOS)
  gray=image.convert('L').resize((17,16),Image.Resampling.LANCZOS);pixels=gray.tobytes();bits=0
  for y in range(16):
   for x in range(16):bits=(bits<<1)|int(pixels[y*17+x]>pixels[y*17+x+1])
  colors=list(image.resize((4,4),Image.Resampling.BILINEAR).tobytes())
  return {'pixelHash':hashlib.sha256(small.tobytes()).hexdigest(),'differenceHash':f'{bits:064x}','colors':colors}

def same_portrait(a,b):
 if not a or not b:return False
 if a.get('pixelHash') and a.get('pixelHash')==b.get('pixelHash'):return True
 try:
  distance=(int(a['differenceHash'],16)^int(b['differenceHash'],16)).bit_count()
  colors=a['colors'];other=b['colors']
  return distance<=6 and len(colors)==len(other)==48 and sum(abs(x-y) for x,y in zip(colors,other))/48<=8
 except (KeyError,ValueError,TypeError):return False

def canonical_url(url):
 parts=urlsplit(url);query=urlencode([(k,v) for k,v in parse_qsl(parts.query) if not re.match(r'utm_|^(?:ref|from|referrer)$',k,re.I)])
 return urlunsplit((parts.scheme.casefold(),parts.netloc.casefold(),parts.path.rstrip('/'),query,''))

def normalized_definition(record):
 body='\n'.join(str(record.get(k) or '') for k in ('prompt','greet'))
 name=str(record.get('name') or '').strip()
 if len(name)>=3:body=re.sub(re.escape(name),' ROLE ',body,flags=re.I)
 body=unicodedata.normalize('NFKC',body).casefold()
 body=re.sub(r'\[(?:description|personality|example dialogue|system prompt)\]',' ',body)
 return re.sub(r'\W+',' ',body).strip()

def duplicate_reason(a,b):
 if canonical_url(a.get('source',''))==canonical_url(b.get('source','')) and a.get('source'):return '同一来源链接'
 aa=a.get('avatarAsset') or {};bb=b.get('avatarAsset') or {}
 if aa.get('sha256') and aa.get('sha256')==bb.get('sha256'):return '相同头像文件'
 if same_portrait(aa.get('visualSignature'),bb.get('visualSignature')):return '同一人物头像（忽略PNG元数据 / 轻微压缩差异）'
 left=normalized_definition(a);right=normalized_definition(b)
 if min(len(left),len(right))<200:return None
 if left==right:return '相同人设和开场（忽略名称与排版）'
 # Five-word shingles remain robust to small attribution/name edits.
 def shingles(value):
  words=value.split()[:5000]
  return {tuple(words[i:i+5]) for i in range(len(words)-4)}
 x=shingles(left);y=shingles(right)
 if x and y and len(x&y)/len(x|y)>=0.93:return '高度重复的人设与开场'
 return None
