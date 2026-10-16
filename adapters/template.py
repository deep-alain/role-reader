"""Copy this file and implement only public or explicitly authorized reading routes."""
class Adapter:
 def __init__(self,options):self.options=options
 def catalogue(self,page=1,limit=20):
  # Return {items:[{url, endpoint, ...}], nextPage: int | None}.
  # SourceRegistry attaches your configured source ID to each item.
  raise NotImplementedError('请实现来源目录接口')
 def read(self,item):
  # Return {record:<pool_sources.card_to_record(...)>, image:<optional bytes>}.
  # Set record.catalogueSource and metricsSource to your configured adapter ID.
  # Keep missing fields empty and missing metrics None.
  raise NotImplementedError('请实现公开角色卡读取')
 def read_metadata(self,record):
  raise NotImplementedError('该来源尚未提供热度更新')

def create_adapter(options):return Adapter(options)
