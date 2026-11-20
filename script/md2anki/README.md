# md2anki — Markdown → Anki 同步工具

将本地 Markdown 文件解析，自动新增/更新 Anki 卡片；支持普通问答卡片、Cloze挖空卡片，管理卡组、标签、卡片暂停，可预览差异、清理过期笔记与空卡组。基于 Anki‑Connect 与 Anki 客户端通信。

> 
> 核心设计理念：**Markdown 为唯一真相源，Anki仅作为复习库**，通过`uuid`作为外部主键绑定笔记，重装、迁移Anki不丢失绑定关系。

## 📦 环境准备

### 依赖安装

```
pip install requests
```

### Anki 准备

1. Anki客户端安装插件 [Anki‑Connect](https://foosoft.net/projects/anki-connect/)
2. **Anki软件必须保持打开**，默认通信地址 `[http://localhost:8765](http://localhost:8765)`
3. 手动创建两套笔记模型，**字段全部小写**

| 模型名称 | 字段列表（小写） | 用途 |
| --- | --- | --- |
| `Basic‑md2anki` | `front`、`back`、`uuid`、`source` | 普通问答卡片 |
| `Cloze‑md2anki` | `text`、`extra`、`uuid`、`source` | 挖空卡片 |

- `uuid`：脚本唯一绑定主键，**不要在复习模板渲染显示**；尽量在编辑界面隐藏，禁止手动修改。
- `source`：**程序自动填充**，保存来源markdown相对文件名，用户无需手写，不渲染到卡片。

> 
> ⚠️Cloze‑md2anki模型必须**克隆Anki原生Cloze填空题模型**，不要拿Basic模型直接修改，否则挖空语法失效。

### ✂️ Anki卡片模板配置

#### Basic‑md2anki

**正面模板 Front**

```
{{front}}
```

**背面模板 Back**

```
{{FrontSide}}

<hr id=answer>

{{back}}
```

**样式 Styling**

```
.card {
 font-family: arial;
 font-size: 20px;
 text-align: center;
 color: black;
 background-color: white;
}
```

#### Cloze‑md2anki

**正面模板 Front**

```
{{cloze:text}}
```

**背面模板 Back**

```
{{cloze:text}}

{{#extra}}
<hr>
{{extra}}
{{/extra}}
```

**样式同Basic**

> 
> 重要：正反面模板**禁止写 `{{uuid}}`、`{{source}}`**，复习时不会看到元数据字段。

### 🛡️进阶防护（强烈推荐）

原生Anki无法禁用字段编辑，安装插件 `Editor Focus Fields`（插件ID：`1140367106`）

1. 工具 → 附加组件 → 获取附加组件，输入ID安装，重启Anki
2. 分别对两个模型配置字段布局：
   - Basic‑md2anki：只勾选显示`front`、`back`，取消勾选`uuid`、`source`
   - Cloze‑md2anki：只勾选显示`text`、`extra`，取消勾选`uuid`、`source`
   效果：浏览编辑笔记时隐藏`uuid`/`source`输入框，极大降低误改风险；需要排查问题可以临时打开全部字段。

> 
> 折中方案（不装插件）：在字段描述填写警告：`【脚本内部主键，请勿手动修改！修改会导致Markdown同步失效】`。

## 📝 Markdown书写语法

脚本扫描目标目录全部`*.md`，支持两种书写格式：**裸代码块格式（推荐）**、**简易短横线 `-`行格式**。

### 公共语法说明

1. `%% 注释内容 %%`：脚本注释，**不会同步到Anki**，支持跨行；块内正文、简易行正文、简易行下方附加段落全部生效。

```
这是卡片正文 %% 这里是仅本地的备注，不会进入anki %%
```

2. Cloze挖空：Markdown写`{{内容}}`，脚本统一转为`{{c1::内容}}`；**同卡片所有挖空同时显示/隐藏，只生成1张子卡片**。> 
> 修改markdown减少挖空数量后，Anki会残留空卡片；菜单：`工具 → 空卡片`手动清理。

### 格式1：裸代码块格式（推荐，适合长内容、多标签、图片）

> 
> 规则：
> 
> 
> 1. 卡组单独一行，前缀固定`#toanki/`；`@suspend`写在该行末尾空格分隔，控制卡片暂停；**块内部不再识别`@suspend`**
> 2. 紧随一行裸 ``` 开启代码块
> 3. 块头部：`Tags:`、`uuid:`；
> 4. `---` 分割正面内容 / extra附加内容；
> 5. ```
> 
> ```

```
#toanki/计算机1/TC @suspend
```

## Tags: 面试,tcp,拥塞控制
uuid:hhffghh111
TCP三次握手是什么 %% 本地备注，不会同步Anki %%

1.客户端SYN
2.服务端SYN+ACK
3.客户端ACK

```

```

字段说明：

- `#toanki/计算机1/TC @suspend`：卡组路径；`@suspend`表示卡片同步后设置暂停；去掉则不暂停。
- `Tags:`：标签，支持英文逗号、中文逗号、空格分隔。
- `uuid:`：**必填全局唯一ID**；修改markdown内容但保持uuid不变 → 更新旧笔记；更换uuid视为新建笔记。
- `---`：分割问答正面 / extra附加内容。

### 格式2：简易短横线 `-`行格式（适合快速随手写卡片）

> 
> `@suspend`写在行内html注释内部；卡组标记`#toanki/层级/子级`。

```
- 艾宾浩斯遗忘曲线的提出时间是{{1885年}},{{1882年}}。 #toanki/心理学/侧2 <!-- uuid: qqq1731945600123 -->
  - 遗忘先快后慢
  - 需要在遗忘临界点复习

- 逻辑回归损失函数::交叉熵损失 #toanki/机器学习 <!-- uuid: eee1731945600123; suspend; tags:AI,算法 -->
```

规则说明：

1. `- `开头；
2. `#toanki/xxx/xxx` 指定卡组；
3. `<!-- uuid:xxx; suspend; tags: t1,t2 -->`行内注释：
   - `uuid`：必填唯一id；
   - `suspend`：可选，存在即暂停卡片；
   - `tags:`：可选标签；
4. `::`分割Basic卡片正面和背面；
5. 正文出现`{{xxx}}`自动识别为Cloze挖空卡片；
6. 下方缩进` -`多行内容，合并作为back/extra附加内容；
7. `%% 注释%%`在正文、附加段落均可使用。

### 🖼️图片支持

1. 本地md图片 `![alt](./xxx.png)`：正式推送模式上传Anki媒体库，替换为`<img>`标签；预览模式仅打印检测日志；
2. Base64内嵌图片：自动解码上传Anki媒体；
3. http/https网络图片：原样保留，**不上传处理**；
4. 代码块闭合行后紧跟图片（不能有空行）自动追加到卡片extra。

> 
> ⚠️脚本只上传媒体文件，**不会自动清理Anki媒体库废弃图片**。

## ⚙️命令行参数

```
# 🔍差异预览模式（推荐优先执行！只读取Anki，不做任何修改）
python3 md2anki.py ./notes --diff

# 普通预览模式：解析打印卡片，不改动Anki
python3 md2anki.py ./notes

# 正式写入Anki
python3 md2anki.py ./notes --toanki

# 正式写入 + 允许删除md不存在笔记（交互确认删除）
python3 md2anki.py ./notes --toanki --allow-delete

# 正式写入 + 删除并且跳过交互确认（⚠️谨慎！直接删除过期笔记）
python3 md2anki.py ./notes --toanki --allow-delete -y
```

| 参数 | 说明 |
| --- | --- |
| `dir` | 笔记目录，默认`./notes`，递归扫描全部`.md` |
| `--diff` | **差异预览模式**，输出新增/更新/待删除清单；只读，不修改Anki |
| `--toanki` | 开启真实写入Anki；不加仅本地预览解析结果 |
| `--allow‑delete` | 开启过期笔记删除逻辑，同时自动清理空卡组；删除前输出json备份文件 |
| `-y / --yes` | 搭配`--allow‑delete`，跳过交互式确认，直接执行删除，谨慎使用 |
| `--max‑delete N` | 单次运行最大允许删除笔记数量，默认20，`0`代表无上限；防止目录配置错误大量误删 |
| `--quiet` | 安静模式，减少debug打印输出 |
| `--append‑tags tag1,tag2` | 全局追加标签，每张卡片自动合并，默认`bear‑app` |
| `--tsv FILE` | 导出tsv文件路径，默认`anki_export.tsv` |
| `--media‑out‑dir DIR` | 可选，将解析出的图片额外保存到此本地文件夹 |

## 🧠同步逻辑

1. 依靠模型字段`uuid`匹配Anki笔记：
   - md中全新uuid → addNote新建笔记；`source`字段自动写入来源md文件名。
   - md中已存在uuid → updateNoteFields更新字段、更新标签、移动卡组；`source`同步更新。
2. suspend标记：全部卡片解析处理完成后，**批量执行暂停/恢复卡片**。
3. `--diff`模式：读取Anki现有笔记，对比本地md，输出新增、更新、待删除清单；**只读，不写删Anki**。
4. 模型变更检测（重要限制：Anki‑Connect**不能切换笔记模型**）
   - 同一个`uuid`，旧笔记模型Basic，本次解析为Cloze（或反过来），脚本告警并**直接跳过该卡片，不做更新/新建**。
   - 解决方式：手动在Anki删除旧笔记，再重新同步；**旧笔记复习记录会丢失**。
5. `--allow‑delete`逻辑：
   - 查询Anki全部带`uuid`字段的笔记；md文件不存在该uuid视为过期笔记；
   - 删除前自动生成备份json文件 `anki_delete_backup_时间戳.json`；
   - 需要交互确认（不加`-y`需要输入`DELETE`确认）；
   - 删除笔记后，如果原卡组变空，自动清理空卡组；**父卡组为空仅告警，不会自动删除父卡组**。
6. 重复`uuid`检测：md检测到重复uuid，脚本直接终止，**不推送、不删除、不导出，避免脏数据**。

## 📁文件输出

1. `anki_export.tsv`：每次运行自动导出，用于调试排查；列：`uuid\tfront/text\tback/extra\tdeck\ttags\tsuspend\tsource`
2. `anki_delete_backup_YYYYMMDD_HHMMSS.json`：开启删除且有待删笔记，生成备份，保存待删除笔记全部字段内容。

## ⚠️重要风险与限制（务必阅读）

1. **不要手动修改Anki内`uuid`字段**，一旦修改，脚本会丢失绑定，下次同步会重复新建卡片，复习记录留在旧笔记。建议使用插件隐藏该编辑框。
2. Anki‑Connect限制：**不支持笔记模型在线切换（Basic ↔ Cloze）**；同一条笔记在markdown从问答改为挖空，脚本跳过同步，需要手动删Anki旧笔记，复习进度丢失。
3. Cloze修改挖空数量会产生空卡片：Anki菜单`工具 → 空卡片`手动清理。
4. 脚本只会管理本脚本创建/同步、拥有`uuid`字段的笔记；完全不会触碰Anki中其他普通笔记。
5. 媒体文件仅上传，**不会自动清理废弃图片**。
6. Markdown文件编码强制使用 **UTF‑8**。

## 🐛常见问题排查

1. `cannot create note because it is empty`
   - Anki模型字段名错误；确认模型字段全部小写：`front,back,uuid,source` / `text,extra,uuid,source`；字段名大小写严格区分。
2. 脚本找不到已存在笔记
   - Anki浏览器打开笔记，检查`uuid`字段值，和md内`uuid:`内容**完全一致，无多余空格换行**。
3. 模型变更告警，卡片跳过同步
   - 手动在Anki删除该uuid对应的旧笔记，再重新运行同步。
4. 图片上传失败：检查图片路径是否正确，文件可读。
5. 删除笔记不生效：笔记没有`uuid`模型字段的，脚本完全不会触碰。

## 📂目录结构示例

```
./
├─ md2anki.py
└─ notes/          # md笔记目录
   ├─ demo1.md
   └─ subfolder/
      └─ demo2.md
```

## ✅检查清单（部署核对）

- 模型名称：`Basic‑md2anki`、`Cloze‑md2anki`
- 字段全部小写：`front/back/uuid/source`；`text/extra/uuid/source`
- Cloze模型是克隆系统原生Cloze，不是Basic修改而来
- 卡片正反面模板**没有写`{{uuid}}`、`{{source}}`**
- （推荐）安装`Editor Focus Fields`插件，编辑界面隐藏`uuid`、`source`输入框
- 优先使用`--diff`预览差异，确认后再执行`--toanki`真实同步
- 切换模型（Basic↔Cloze）需要手动删除Anki旧笔记

> 
> 工作流最佳实践：
> 
> 
> 1. 新增/修改md笔记
> 2. `python3 md2anki.py ./notes --diff` 查看差异
> 3. 确认差异无误，执行正式同步
> 4. 需要清理过期笔记再加上`--allow‑delete`参数。
