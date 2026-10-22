"""Install an isolated CPU image-review runtime and pinned public ONNX weights."""
import concurrent.futures,hashlib,json,os,subprocess,sys,time,urllib.request,venv
from pathlib import Path

ROOT=Path(__file__).resolve().parent
MODELS={
 'safety':{'repo':'onnx-community/nsfw_image_detection-ONNX','revision':'1ceb3c7fe1e9f3f2507e6df577437f23a9149fd5','files':{'onnx/model_int8.onnx':'bdc941491cd09ae75c5f40b038a5cd64482fa4e2b5297747323c5cda31303920','config.json':None,'preprocessor_config.json':None,'README.md':None}},
 'clip':{'repo':'Xenova/clip-vit-base-patch32','revision':'d15189d7028b43f1d3e65039190477f6af591c2a','files':{'onnx/vision_model_int8.onnx':'0ab0c1b3ace708e539633af1744d5a95247fe4e14d3e08ff197ef82a6cb9bd93','onnx/text_model_int8.onnx':'18845f2ccc35223bb7fec403383a131154b11ac0918df25cf51986df5efd3a21','config.json':None,'preprocessor_config.json':None,'tokenizer.json':None,'tokenizer_config.json':None,'README.md':None}}
}
WEIGHT_SIZES={'bdc941491cd09ae75c5f40b038a5cd64482fa4e2b5297747323c5cda31303920':87333629,'0ab0c1b3ace708e539633af1744d5a95247fe4e14d3e08ff197ef82a6cb9bd93':88648877,'18845f2ccc35223bb7fec403383a131154b11ac0918df25cf51986df5efd3a21':64070791}
def runtime_python(root=ROOT):return Path(root)/'data'/'vision-runtime'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
def sha256(path):
 digest=hashlib.sha256()
 with Path(path).open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
 return digest.hexdigest()
def install(root=ROOT):
 root=Path(root);python=runtime_python(root)
 if not python.exists():
  print('正在建立独立图像检查环境…',flush=True);venv.EnvBuilder(with_pip=True).create(python.parent.parent)
 check=subprocess.run([str(python),'-c','import onnxruntime,tokenizers,PIL,numpy'],capture_output=True)
 if check.returncode:
  subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','onnxruntime>=1.20,<2','tokenizers>=0.20,<1','Pillow>=10.4','numpy>=1.26'],check=True)
 tasks=[]
 for name,spec in MODELS.items():
  for filename,digest in spec['files'].items():tasks.append((name,spec,filename,digest))
 def download(task):
  name,spec,filename,digest=task;path=root/'data'/'models'/name/filename
  if path.exists() and (not digest or sha256(path)==digest):return
  path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix(path.suffix+'.download')
  url='https://huggingface.co/'+spec['repo']+'/resolve/'+spec['revision']+'/'+filename
  print('正在下载图像检查模型：'+name+'/'+filename,flush=True)
  request=urllib.request.Request(url,headers={'User-Agent':'RoleReader-model-setup/1.11'})
  try:
   if digest in WEIGHT_SIZES:
    size=WEIGHT_SIZES[digest];chunk=4*1024*1024
    with temp.open('wb') as stream:stream.truncate(size)
    def part(start):
     end=min(size,start+chunk)-1;last=None
     for attempt in range(3):
      try:
       req=urllib.request.Request(url+'?download=true',headers={'User-Agent':'RoleReader-model-setup/1.11','Range':f'bytes={start}-{end}'})
       with urllib.request.urlopen(req,timeout=45) as response:
        if response.status!=206 or response.headers.get('Content-Range')!=f'bytes {start}-{end}/{size}':raise ValueError('模型分段下载范围不匹配')
        data=response.read(end-start+2)
       if len(data)!=end-start+1:raise ValueError('模型分段下载不完整')
       with temp.open('r+b') as stream:stream.seek(start);stream.write(data)
       return
      except Exception as error:last=error;time.sleep(.3*(attempt+1))
     raise last
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as parts:list(parts.map(part,range(0,size,chunk)))
   else:
    with urllib.request.urlopen(request,timeout=60) as response,temp.open('wb') as stream:
     total=0
     for block in iter(lambda:response.read(65536),b''):
      total+=len(block)
      if total>400*1024*1024:raise ValueError('单个模型文件超过 400 MB')
      stream.write(block)
   if digest and sha256(temp)!=digest:raise ValueError('模型校验未通过：'+name+'/'+filename)
   os.replace(temp,path)
  finally:temp.unlink(missing_ok=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:list(executor.map(download,tasks))
 manifest={name:{'repo':spec['repo'],'revision':spec['revision'],'files':{filename:sha256(root/'data'/'models'/name/filename) for filename in spec['files']}} for name,spec in MODELS.items()}
 (root/'data'/'models'/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
 print('本机自动图像检查模型已就绪。',flush=True)

if __name__=='__main__':install()
