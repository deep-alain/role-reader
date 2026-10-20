"""Lifetime OS lock for a writable data directory; released on process exit."""
import os
from pathlib import Path

class DirectoryLock:
 def __init__(self,directory):
  path=Path(directory);path.mkdir(parents=True,exist_ok=True)
  self.file=(path/'.writer.lock').open('a+b')
  try:
   self.file.seek(0,2)
   if not self.file.tell():self.file.write(b'0');self.file.flush()
   self.file.seek(0)
   if os.name=='nt':
    import msvcrt
    msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
   else:
    import fcntl
    fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError as error:
   self.file.close();self.file=None
   raise ValueError('该数据目录已有读取器运行，请关闭原进程或使用不同数据目录') from error
 def close(self):
  if self.file is None:return
  self.file.seek(0)
  if os.name=='nt':
   import msvcrt
   msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
  else:
   import fcntl
   fcntl.flock(self.file,fcntl.LOCK_UN)
  self.file.close();self.file=None
 def __del__(self):
  if getattr(self,'file',None):self.close()
