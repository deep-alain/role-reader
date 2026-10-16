# 参与开发

Python 3.10+ 与 Node.js 20+。Pillow 用于解码图片并比较像素，网络与本机服务使用 Python 标准库。

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
python api_server.py --no-browser
```

启动后打开 `http://127.0.0.1:8766/app.html`。`data/` 是用户本机数据，已加入 `.gitignore`。请使用合成角色卡写测试，不提交真实库存、头像缓存、账号令牌和抓取日志。

新渠道参见 [来源适配器说明](docs/SOURCES.md)，可以新增适配器并修改配置，无需改常备库或导出模块。核心接口与通用记录模型见 `source_registry.py`、`pool_sources.py`。适配器应区分可读取、需要授权、作者隐藏和暂时失败，保留未知字段，准确标注热度来源。

提交前运行测试。默认 CI 只跑合成与本机 HTTP 测试，不依赖来源站实时可用性；`tests/live-*.py` 是手动联网检查脚本。
