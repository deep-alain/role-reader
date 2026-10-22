from pathlib import Path
import zipfile,json

root=Path(__file__).resolve().parent
output=root.parent/'outputs'/'角色资料读取器_v1.11.1_类型排除.zip'
output.parent.mkdir(exist_ok=True)
manifest=json.loads((root/'extension'/'manifest.json').read_text(encoding='utf-8'))
assert manifest['manifest_version']==3
modules=['automation_jobs.py','portrait_review.py','setup_automation.py','directory_lock.py','api_server.py','pool_store.py','pool_sources.py','pool_policy.py','pool_avatars.py','source_metrics.py','discovery_sources.py','selection_rules.py','taste_profile.py','preference_learning.py','source_registry.py','duplicate_guard.py','package_tool.py']
files=[root/name for name in modules+['README.md','LICENSE','CONTRIBUTING.md','requirements.txt','package.json','启动接口工具.cmd','接口验证说明.md']]
for folder in ['extension','tests','config','adapters','docs']:
 files.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.local.' not in p.name)
with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
 for path in sorted(files):archive.write(path,'角色资料读取器/'+path.relative_to(root).as_posix())
with zipfile.ZipFile(output) as archive:
 assert archive.testzip() is None
 assert not any('/data/' in name or 'taste_examples.json' in name for name in archive.namelist())
 print(json.dumps({'output':str(output),'files':len(archive.namelist()),'bytes':output.stat().st_size},ensure_ascii=False))
