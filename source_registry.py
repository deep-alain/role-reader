"""Locally configured source adapters. A custom adapter is trusted local Python code."""
import importlib.util,json,re,sys
from pathlib import Path
from pool_sources import PublicCatalogue,SOURCES

class SourceRegistry:
 def __init__(self,fetcher,config_path):
  path=Path(config_path);config=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'builtinSources':list(SOURCES),'adapters':[]}
  builtin=config.get('builtinSources',list(SOURCES))
  if not isinstance(builtin,list) or len(set(builtin))!=len(builtin) or any(s not in SOURCES for s in builtin):raise ValueError('builtinSources 必须为已支持来源的不重复列表')
  self.builtin=PublicCatalogue(fetcher);self.adapters={};self.sources=list(builtin)
  for entry in config.get('adapters',[]):
   if entry.get('enabled',True) is not True:continue
   key=entry.get('id','')
   if not re.fullmatch(r'[a-z][a-z0-9_-]{1,31}',key) or key in self.sources:raise ValueError('适配器 ID 无效或重复')
   module_path=Path(entry['module']);module_path=module_path if module_path.is_absolute() else path.parent/module_path
   module_path=module_path.resolve()
   if module_path.suffix!='.py' or not module_path.is_file():raise ValueError('适配器必须为现有本地 .py 文件')
   spec=importlib.util.spec_from_file_location('role_reader_adapter_'+key,module_path);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
   adapter=module.create_adapter(dict(entry.get('options') or {}))
   if not all(callable(getattr(adapter,name,None)) for name in ('catalogue','read')):raise ValueError('适配器缺少 catalogue 或 read')
   self.adapters[key]=adapter;self.sources.append(key)
  if not self.sources:raise ValueError('至少启用一个来源')
  self.sources=tuple(self.sources)
 def supports_query_override(self,source):return source in self.sources and source not in self.adapters
 def catalogue(self,source,page=1,limit=20,query=None):
  if source not in self.sources:raise ValueError('来源未启用')
  if source not in self.adapters:return self.builtin.catalogue(source,page,limit,query=query)
  result=self.adapters[source].catalogue(page,limit)
  if not isinstance(result,dict) or not isinstance(result.get('items'),list):raise ValueError('适配器目录格式无效')
  return {**result,'items':[{**item,'source':source} for item in result['items']]}
 def read(self,item):
  source=item['source']
  if source not in self.sources:raise ValueError('来源未启用')
  result=self.adapters[source].read(item) if source in self.adapters else self.builtin.read(item)
  if not isinstance(result,dict) or not isinstance(result.get('record'),dict):raise ValueError('适配器详情格式无效')
  result['record']['catalogueSource']=source;result['record'].setdefault('metricsSource',source)
  return result
 def read_metadata(self,record):
  source=record.get('catalogueSource') or record.get('metricsSource')
  if source not in self.sources:raise ValueError('来源未启用，保留原始计数')
  if source in self.adapters:
   reader=getattr(self.adapters[source],'read_metadata',None)
   if reader is None:raise ValueError('适配器未提供热度更新，保留原始计数')
   return reader(record)
  return self.builtin.read_metadata(record)
