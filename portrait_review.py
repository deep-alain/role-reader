"""Local, reproducible image screening. Uncertain/model failures never approve."""
import base64,concurrent.futures,hashlib,json,os,subprocess,sys,threading,time
from pathlib import Path
from setup_automation import MODELS,runtime_python,sha256

ROOT=Path(__file__).resolve().parent
PROMPTS={
 'photo':['a photograph of a clothed adult person, a realistic human portrait','a fashion photograph of a person wearing clothing'],
 'detailed_illustration':['a detailed anime illustration of a clothed adult person','a detailed digital painting of a person wearing clothing'],
 'rendered_portrait':['a realistic 3D rendered portrait of a clothed person','a computer rendered human character wearing clothing'],
 'default_icon':['a default user profile silhouette icon','a generic avatar symbol with headphones'],
 'no_person':['a landscape or abstract image without any people','a picture of an object without a human person'],
 'chibi_cartoon':['a tiny chibi cartoon character with a large head and small body','a simplistic cartoon video game character sticker'],
 'faint_sketch':['an uncolored pencil sketch with faint outlines of a person','a simple monochrome line drawing without detailed color'],
 'explicit':['an image containing nudity and exposed intimate body parts','an image of an explicit sexual act'],
}
ALLOWED={'photo','detailed_illustration','rendered_portrait'}

def decide(risk,scores,config=None):
 """Cosine margins are relative model evidence, not safety probabilities."""
 import math
 config=config or {};ranked=sorted(scores.items(),key=lambda pair:pair[1],reverse=True)
 if not ranked or not math.isfinite(risk) or not 0<=risk<=1 or any(not math.isfinite(v) for _,v in ranked):return {'decision':'defer','reason':'图像模型输出无效'}
 label,score=ranked[0];style_margin=score-ranked[1][1] if len(ranked)>1 else 0
 opposite=[value for kind,value in ranked if (kind in ALLOWED)!=(label in ALLOWED)]
 margin=score-max(opposite) if opposite else 0
 evidence={'nsfwRisk':round(risk,5),'style':label,'similarity':round(score,5),'margin':round(margin,5),'styleMargin':round(style_margin,5),'scores':{k:round(v,5) for k,v in ranked},'thresholds':{'maximumNsfwRisk':config.get('maximumNsfwRisk',.10),'minimumPortraitSimilarity':config.get('minimumPortraitSimilarity',.23),'minimumStyleMargin':config.get('minimumStyleMargin',.025)}}
 if score<config.get('minimumPortraitSimilarity',.23) or margin<config.get('minimumStyleMargin',.025):return {'decision':'defer','reason':'人像或画风判断不清，保留待确认','evidence':evidence}
 if label not in ALLOWED:return {'decision':'reject','style':'other_rejected' if label=='explicit' else label,'reason':'自动图像筛选未通过：'+label,'evidence':evidence}
 if risk>config.get('maximumNsfwRisk',.10):return {'decision':'defer','reason':'图像风险模型有疑虑，未自动放行','evidence':evidence}
 medium={'photo':'photorealistic','detailed_illustration':'illustration','rendered_portrait':'rendered'}[label] if style_margin>=.015 else None
 if medium is None:label='portrait'
 return {'decision':'accept','style':label,'medium':medium,'reason':'本机模型人像与图像风险检查通过','evidence':evidence}

class LocalPortraitReviewer:
 def __init__(self,directory,root=ROOT):
  self.root=Path(root);self.directory=Path(directory);self.lock=threading.RLock();self.process=None;self.preparing=False;self.note='本机图像模型尚未加载';self.error='';self.prepared=False;self.disabled=False
  path=self.root/'config'/'automation.json';self.config=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
  self.disabled=self.config.get('autoPortraitReview',True) is not True
  self.cache_path=self.directory/'portrait_automation.json';self.cache={}
  if self.cache_path.exists():
   try:self.cache=json.loads(self.cache_path.read_text(encoding='utf-8'))
   except (ValueError,OSError):pass
  signature={'version':2,'models':{k:v['revision'] for k,v in MODELS.items()},'config':self.config,'prompts':PROMPTS}
  self.signature=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
 def available(self):
  if self.prepared and self.process is not None and self.process.poll() is not None:self.prepared=False;self.error='本机图像检查进程已退出，请重新开始任务加载模型'
  return self.prepared and not self.disabled
 def snapshot(self):return {'available':self.available(),'backend':'local-onnx-cpu','preparing':self.preparing,'note':self.error or self.note,'modelVersion':self.signature[:16],'scope':'本机图像风险、人像及画风筛选；不推断年龄或性别，疑难结果保留待确认'}
 def _files_ready(self):
  if not runtime_python(self.root).exists():return False
  for name,spec in MODELS.items():
   for filename,digest in spec['files'].items():
    path=self.root/'data'/'models'/name/filename
    if not path.exists() or digest and sha256(path)!=digest:return False
  return True
 def prepare(self,cancel_event=None):
  with self.lock:
   if self.available():return True
   if self.disabled:self.note='自动头像检查已在本机配置关闭';return False
   self.preparing=True;self.error=''
   try:
    if not self._files_ready():
     self.note='首次使用，正在下载约 240 MB 的本机图像模型'
     log_path=self.directory/'vision-setup.log';self.directory.mkdir(parents=True,exist_ok=True)
     with log_path.open('ab') as log:
      process=subprocess.Popen([sys.executable,str(self.root/'setup_automation.py')],cwd=self.root,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
      deadline=time.monotonic()+900
      while process.poll() is None:
       if cancel_event and cancel_event.is_set() or time.monotonic()>deadline:process.terminate();process.wait();raise RuntimeError('模型准备已取消或超时')
       time.sleep(.2)
      if process.returncode or not self._files_ready():raise RuntimeError('图像模型准备失败，详情见本机 vision-setup.log')
    self.note='正在加载本机 CPU 图像模型'
    self.process=subprocess.Popen([str(runtime_python(self.root)),str(self.root/'portrait_review.py'),'--worker'],cwd=self.root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',bufsize=1,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    response=self._readline(120)
    if not response.get('ready'):raise RuntimeError(response.get('error','图像模型加载失败'))
    self.prepared=True;self.note='本机图像模型已就绪；高置信度人像自动检查，疑难项保留待确认';return True
   except Exception as error:
    self.error=str(error)[:240];self.close();return False
   finally:self.preparing=False
 def _readline(self,timeout):
  # A timed reader prevents a stalled inference from blocking the whole workflow.
  executor=concurrent.futures.ThreadPoolExecutor(max_workers=1)
  future=executor.submit(self.process.stdout.readline)
  try:
   line=future.result(timeout=timeout)
   if not line:raise RuntimeError('本机图像检查进程已退出')
   return json.loads(line)
  except concurrent.futures.TimeoutError:
   self.close();raise RuntimeError('本机图像推理超时') from None
  finally:executor.shutdown(wait=False,cancel_futures=True)
 def __call__(self,record):
  from pool_avatars import avatar_file
  if not self.available():return {'decision':'defer','reason':self.snapshot()['note']}
  with self.lock:
   try:
    data,_=avatar_file(self.directory,record);digest=hashlib.sha256(data).hexdigest();key=self.signature+':'+digest
    if key in self.cache:return self.cache[key]
    self.process.stdin.write(json.dumps({'image':base64.b64encode(data).decode(),'config':self.config})+'\n');self.process.stdin.flush()
    result=self._readline(self.config.get('inferenceTimeoutSeconds',90))
    if result.get('decision') not in ('accept','reject','defer'):raise ValueError('图像模型没有返回有效结果')
    if result.get('evidence'):
     result['evidence'].update(sha256=digest,modelVersion=self.signature,models={k:v['repo']+'@'+v['revision'] for k,v in MODELS.items()},checkedAt=time.time())
    self.cache[key]=result
    temp=self.cache_path.with_suffix('.tmp');temp.write_text(json.dumps(self.cache,ensure_ascii=False),encoding='utf-8');os.replace(temp,self.cache_path)
    return result
   except Exception as error:
    if self.process is not None and self.process.poll() is not None:self.prepared=False;self.error='本机图像检查进程已退出，请重新开始任务加载模型'
    return {'decision':'defer','reason':'自动图像检查失败：'+str(error)[:180]}
 def close(self):
  self.prepared=False
  if self.process:
   if self.process.poll() is None:self.process.terminate()
   try:self.process.wait(timeout=5)
   except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
   for stream in (self.process.stdin,self.process.stdout):
    if stream:stream.close()
   self.process=None

def worker():
 import io,numpy as np,onnxruntime as ort
 from PIL import Image,ImageOps
 from tokenizers import Tokenizer
 options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
 def session(path):return ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
 safety=session(ROOT/'data/models/safety/onnx/model_int8.onnx');vision=session(ROOT/'data/models/clip/onnx/vision_model_int8.onnx');text=session(ROOT/'data/models/clip/onnx/text_model_int8.onnx')
 tokenizer=Tokenizer.from_file(str(ROOT/'data/models/clip/tokenizer.json'));tokenizer.enable_truncation(max_length=77);tokenizer.enable_padding(pad_id=49407,pad_token='<|endoftext|>')
 prompts=[p for values in PROMPTS.values() for p in values];encoded=tokenizer.encode_batch(prompts)
 candidates={'input_ids':np.array([v.ids for v in encoded],dtype=np.int64),'attention_mask':np.array([v.attention_mask for v in encoded],dtype=np.int64)}
 def embedding(session,inputs,name):
  names=[v.name for v in session.get_outputs()];value=session.run([name] if name in names else None,inputs)[0]
  if value.ndim!=2 or value.shape[1]!=512 or not np.isfinite(value).all():raise ValueError('CLIP 投影形状无效')
  return value/np.maximum(np.linalg.norm(value,axis=-1,keepdims=True),1e-12)
 vectors=embedding(text,{v.name:candidates[v.name] for v in text.get_inputs()},'text_embeds')
 means=[];offset=0
 for values in PROMPTS.values():
  vector=vectors[offset:offset+len(values)].mean(axis=0);means.append(vector/np.linalg.norm(vector));offset+=len(values)
 means=np.stack(means)
 print(json.dumps({'ready':True}),flush=True)
 for line in sys.stdin:
  try:
   request=json.loads(line);image=ImageOps.exif_transpose(Image.open(io.BytesIO(base64.b64decode(request['image'],validate=True)))).convert('RGB')
   safe=np.asarray(image.resize((224,224),Image.Resampling.BILINEAR),dtype=np.float32)/255
   safe=((safe-.5)/.5).transpose(2,0,1)[None,...]
   logits=safety.run(None,{safety.get_inputs()[0].name:safe})[0][0];p=np.exp(logits-logits.max());risk=float((p/p.sum())[1])
   w,h=image.size;scale=224/min(w,h);resized=image.resize((round(w*scale),round(h*scale)),Image.Resampling.BICUBIC);left=(resized.width-224)//2;top=(resized.height-224)//2
   clip=np.asarray(resized.crop((left,top,left+224,top+224)),dtype=np.float32)/255
   clip=((clip-np.array([.48145466,.4578275,.40821073],dtype=np.float32))/np.array([.26862954,.26130258,.27577711],dtype=np.float32)).transpose(2,0,1)[None,...]
   vector=embedding(vision,{vision.get_inputs()[0].name:clip},'image_embeds')[0];scores=dict(zip(PROMPTS,map(float,means@vector)))
   result=decide(risk,scores,request.get('config'))
  except Exception as error:result={'decision':'defer','reason':'本机推理失败：'+str(error)[:180]}
  print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__' and '--worker' in sys.argv:
 try:worker()
 except Exception as error:print(json.dumps({'ready':False,'error':str(error)[:200]}),flush=True)
