# 更换数据渠道

编辑 `config/sources.json`，重新启动服务。读取器、常备库、头像筛选与导出代码都在本仓库，没有绑定必须使用的远程服务。

`builtinSources` 可选 `chub`、`botbooru`、`charavault`；删除名字即可关闭该来源。至少保留一个已启用来源（内置或自定义）。关闭来源不会删除已经读取的资料。

新增渠道：复制 `adapters/template.py`，实现 `catalogue(page, limit)` 和 `read(item)`。然后把下面配置加入 `adapters` 数组：

```json
{
  "id": "my_source",
  "module": "../adapters/my_source.py",
  "enabled": true,
  "options": {"base_url": "https://your-authorized-source.example"}
}
```

目录返回 `{ "items": [{"url": "角色页面", "endpoint": "读取入口"}], "nextPage": 2 }`。末页 `nextPage` 为 `null`。Registry 会填写 `item.source`，其他参数保留给你的适配器。

详情返回 `{"record": record, "image": optional_bytes}`。`record` 可以由 `pool_sources.card_to_record(card, source_url, avatar_url, method)` 创建，包含九项角色字段，以及 `catalogueSource`、`metricsSource`、`sourceMetrics`、`sourceRating`。热度使用 `views`、`messages`、`chats`、`downloads`、`favorites`、`likes` 等独立字段；没有的数据设为 `None`，不要把消息数改名为浏览量。图片字节可通过 `image` 返回，这样自定义图片渠道无需修改内置图片域名白名单。

`read_metadata(record)` 可选，用于定期刷新分级与真实热度。返回带 `sourceMetrics`、`sourceRating` 和 `metricsAt` 的字典。未实现则保留上次数据并显示原因。适配器可自行使用标准库网络请求；应使用公开或已经取得授权的入口，遵守来源的访问约束。浏览器通用页面读取仍可使用附带扩展。

自定义适配器是用户自行安装的本地 Python 程序，拥有当前服务进程的权限。只配置你信任的文件；工作台不接受远程上传和执行适配器。不要把令牌写入 Git 跟踪的配置文件，使用本机环境变量。

未知头像先进入“待核验”，不计入 50 个待用名额。检查为不露点的人像后，选择风格通过；默认图标、非人像、过淡线稿和 Q 版卡通不进入常备库。精细动漫人物插画仍可通过，不能把所有 Anime 标签一刀切删除。具体偏好与审核结果保存在本机 `data/taste_profile.json`，与 GitHub 仓库分开。
