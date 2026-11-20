# md2anki — Markdown → Anki 同步工具

将本地 Markdown 文件解析，自动新增/更新 Anki 卡片；支持普通问答卡片、Cloze挖空卡片，管理卡组、标签、卡片暂停，可清理过期笔记与空卡组。通过 Anki‑Connect 和 Anki 通信。

## 环境准备

### 依赖安装

```
pip install requests
```

### Anki 准备

1. Anki 客户端安装插件 [Anki‑Connect](https://foosoft.net/projects/anki-connect/)
2. Anki 软件**必须保持打开状态**，默认通信地址 `[http://localhost:8765](http://localhost:8765)`
3. 在Anki中手动创建两套笔记模型，字段全部小写：

| 模型名称 | 字段列表（小写） |
| --- | --- |
| `Basic‑md2anki` | `front`、`back`、`uuid` |
| `Cloze‑md2anki` | `text`、`extra`、`uuid` |

> 
> `uuid`：脚本的唯一标识字段，**不要在卡片模板上渲染显示**，仅用于脚本定位笔记。

## Markdown 书写语法

> 
> 脚本扫描目标目录下全部 `*.md` 文件，支持两种格式：**裸代码块格式**、**简易短横线 `-` 行格式**。

### 格式1：裸代码块格式（推荐）

> 
> 规则：
> 
> 
> 1. 卡组定义单独一行，前缀固定 `#toanki/`；`@suspend` 写在该行末尾空格分隔，控制卡片是否暂停；
> 2. 紧随一行是裸 ``` 开启代码块；
> 3. 块内头部：`Tags:`、`uuid:`；**块内部不再识别 `@suspend`**；
> 4. `---` 分割正面内容与附加内容extra；
> 5. `{{内容}}` 会被自动转为 Anki cloze挖空语法，**全部固定为 c1，多张挖空同时显示隐藏**。

```
#toanki/计算机1/TC @suspend
```

## Tags: 面试,tcp,拥塞控制
uuid:hhffghh111
这里是问题front1111

这里是答案back
好多内容

```

```

- `#toanki/计算机1/TC @suspend`：卡组路径，`@suspend` 代表卡片新建/更新后设置为暂停；去掉则不暂停；
- `Tags:`：标签，支持英文逗号、中文逗号、空格分隔；
- `uuid:`：**必填全局唯一ID**，修改md内容不改动uuid就是更新旧卡片；更换uuid视为新建卡片；
- `---`：分割问答正面 / extra附加内容；
- 块闭合 ``` 之后紧跟图片行（无空行），图片自动追加到extra字段。

### 格式2：简易短横线 `-` 行格式

> 
> 适合写单行卡片；`@suspend` 在 html注释内部声明；卡组标记 `#toanki/层级/子级`。

```
- 艾宾浩斯遗忘曲线的提出时间是{{1885年}},{{1882年}}。 #toanki/心理学/侧2 <!-- uuid: qqq1731945600123 -->
  - 更多解释
  - 更多哦

- 逻辑回归损失函数::交叉熵损失 #toanki/机器学习 <!-- uuid: eee1731945600123; suspend; tags:AI,算法 -->
```

规则说明：

1. `- ` 开头；
2. `#toanki/xxx/xxx` 指定卡组；
3. `<!-- uuid:xxx; suspend; tags: t1,t2 -->` 行内注释：
   - `uuid`：必填唯一id；
   - `suspend`：可选，存在即暂停卡片；
   - `tags:`：可选标签；
4. `::` 分割普通Basic卡片的正面和背面；
5. 正文出现 `{{xxx}}` 自动识别为Cloze挖空卡片；
6. 下方缩进 ` -` 的多行内容，合并作为back/extra附加内容。

### 图片支持

1. 本地md图片 `![alt](./xxx.png)`：正式推送模式下上传Anki媒体库，替换为`<img>`标签；预览模式仅打印检测日志；
2. Base64内嵌图片：自动解码上传Anki媒体；
3. http/https网络图片：原样保留，**不上传处理**；
4. 代码块闭合行后紧跟图片（不能有空行）自动追加到卡片extra。

> 
> 脚本不会自动清理Anki媒体库里的废弃图片。

## 命令行参数

```
#预览模式：只解析打印，**完全不会修改Anki**
python3 md2anki.py ./notes

#正式写入Anki
python3 md2anki.py ./notes --toanki

#正式写入 + 允许删除md中不存在的笔记（交互确认删除）
python3 md2anki.py ./notes --toanki --allow-delete

#正式写入 + 删除并且跳过交互确认（谨慎！直接删除过期笔记）
python3 md2anki.py ./notes --toanki --allow-delete -y
```

| 参数 | 说明 |
| --- | --- |
| `dir` | 笔记目录，默认 `./notes`，扫描目录下全部`.md` |
| `--toanki` | 开启真实写入Anki；不加只预览解析结果 |
| `--allow‑delete` | 开启过期笔记删除逻辑，同时自动清理空卡组；删除前输出json备份文件 |
| `-y / --yes` | 搭配`--allow‑delete`，跳过交互式确认，直接执行删除，谨慎使用 |
| `--max‑delete N` | 单次运行最大允许删除笔记数量，默认20，0=无上限，防止目录配置错误大量误删 |
| `--quiet` | 安静模式，减少debug打印输出 |
| `--append‑tags tag1,tag2` | 全局追加标签，每张卡片自动合并，默认`bear‑app` |
| `--tsv FILE` | 导出tsv文件路径，默认`anki_export.tsv` |
| `--media‑out‑dir DIR` | 可选，将解析出的图片额外保存到此本地文件夹 |

## 同步逻辑

1. 依靠模型字段`uuid`匹配Anki笔记：
   - md中全新uuid → addNote新建笔记；
   - md中已经存在的uuid → updateNoteFields更新字段、更新标签、移动卡组；
2. suspend标记：全部卡片处理完成后，批量执行暂停/恢复卡片；
3. `--allow‑delete`逻辑：
   - 查询Anki中全部带有`uuid`字段的笔记；md文件不存在该uuid，视为过期笔记；
   - 删除前自动生成备份json文件 `anki_delete_backup_时间戳.json`；
   - 交互确认（不加 `-y` 需要输入`DELETE`确认）；
   - 删除笔记之后，如果原卡组变为空，自动清理空卡组；**父卡组为空仅告警，不会自动删除父卡组**；
4. **重复uuid检测**：md文件内部检测重复uuid，一旦发现脚本直接终止，不推送、不删除、不导出，避免脏数据。

## Cloze挖空重要提示

脚本将所有 `{{内容}}` 统一输出为 `{{c1::内容}}`。

> 
> 全部c1意味着：**一张卡片内所有挖空同时显示/同时隐藏，只生成1张子卡片**。

> 
> 如果修改Markdown减少挖空数量，Anki会残留旧的空卡片；
> 处理方式：Anki顶部菜单：`工具 → 空卡片`，手动清理多余卡片。

## 文件输出

1. `anki_export.tsv`：每次运行自动导出，用于调试排查；列：`uuid\tfront/text\tback/extra\tdeck\ttags\tsuspend`
2. `anki_delete_backup_YYYYMMDD_HHMMSS.json`：开启删除并且存在待删笔记，生成备份，保存待删除笔记全部字段内容。

## 常见问题排查

1. `cannot create note because it is empty`
   - Anki模型字段名错误；确认模型字段全部小写：`front,back,uuid` / `text,extra,uuid`。字段名大小写严格区分。
2. 找不到旧笔记：检查Anki笔记`uuid`字段内容，和md内`uuid:`内容完全一致，无多余换行空格。
3. SyntaxWarning转义警告：代码已处理完毕；
4. 图片上传失败：检查图片路径是否正确，文件可读；
5. 删除笔记不生效：笔记没有`uuid`模型字段的，脚本完全不会触碰。

> 
> ⚠️注意：脚本只会管理本脚本创建/同步的、拥有`uuid`字段的笔记，不会修改、触碰Anki里面其他普通笔记。

## 目录结构示例

```
./
├─ md2anki.py
└─ notes/
   ├─ demo1.md
   └─ subfolder/
      └─ demo2.md
```

## 限制与注意事项

1. Markdown文件编码强制使用 UTF‑8；
2. Anki‑Connect必须开启，Anki客户端软件保持打开；
3. `-y`参数跳过确认直接删除笔记，使用前务必不加 `-y`，先观察待删除列表；
4. cloze修改挖空数量后，需要手动执行空卡片清理；
5. 媒体文件只上传，不会自动清理废弃图片。
