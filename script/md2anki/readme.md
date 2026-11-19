# md2anki 同步脚本 README

> 
> Markdown → Anki 双向同步脚本，基于 AnkiConnect，使用 `<anki>` 标签管理卡片

## ✨ 功能特性

1. **从 Markdown 目录批量导入卡片**
   - 识别 md 文件内 `<anki id="" type="" tags="">` 区块
   - 支持两种卡片类型：`basic`（正反面）、`cloze`（填空挖空）
   - 自动映射 `#anki/a/b` 格式为 Anki 层级卡组 `a::b`
   - 不存在的卡组自动创建
2. **增量更新卡片**
   - 根据 `id(uuid)` 作为卡片唯一标识
   - 相同 uuid：更新字段、标签、移动到目标卡组
   - 新 uuid：新增笔记
3. **清理逻辑（--allow-delete）【重点规则】**
   - 只删除**本次md中不存在uuid对应的笔记**
   - 笔记删除后：**仅检查这批笔记原来所属的卡组**，其余卡组完全不扫描、不改动
   - 叶子子卡组：删笔记后为空 → 自动删除该子卡组
   - 向上递归遍历它所有父卡组：判断是否为空，**仅打印告警提示，不会自动删除父卡组**
4. **导出 TSV**
 每次运行自动输出 `anki_export.tsv`，方便备份、核对卡片
5. **预览模式**
 不加 `--toanki`，只解析打印卡片，不会修改 Anki

## 📋 前置依赖

1. Anki 安装插件：[AnkiConnect](https://foosoft.net/projects/anki-connect/)
2. Anki 中提前建好两个笔记类型（model），名称严格一致：
   - `Basic-md2anki`：字段 `front, back, uuid`
   - `Cloze-md2anki`：字段 `text, extra, uuid`
3. Python 依赖包

```
pip install pyyaml requests
```

## 📝 Markdown 写法示例

### Basic 卡片

```
<anki id="note_001" type="basic" tags="python,基础">
deck: #anki/python/基础
front: 什么是 Python ?
back: Python 是一门解释型高级编程语言
</anki>
```

### Cloze 挖空卡片

```
<anki id="note_002" type="cloze" tags="linux">
deck: #anki/linux/命令
text: ls 命令用于{{c1::列出目录内容}}
extra: 常用参数 -l 长格式
</anki>
```

> 
> ✅ deck 路径 `#anki/a/b` 会自动转为 Anki 卡组 `a::b`

## 🚀 运行命令

### 预览（推荐先跑这个，不改动Anki）

```
python script.py ./notes
```

### 正式推送，写入Anki，不开启删除

```
python script.py ./notes --toanki
```

### 正式推送 + 开启清理（删除md不存在笔记 + 空子卡组自动清理、空父卡组仅提示）

```
python script.py ./notes --toanki --allow-delete
```

### 安静模式（关闭调试打印）

```
python script.py ./notes --toanki --allow-delete --quiet
```

## ⚠️ 清理逻辑详细说明（--allow-delete）

举例子：

- md 删掉 `数学::数学2` 下全部卡片

1. 脚本删除该uuid笔记
2. 只检查卡组 `数学::数学2`
3. `数学::数学2` 无卡片 → **自动删除这个子卡组**
4. 向上找到父卡组 `数学`，检查 `数学` 是否为空
5. 如果父卡组为空 → 控制台打印警告，**不会删除父卡组**
6. 其他无关卡组（英语、物理等）全程不触碰

> 
> 规则一句话：**本次删了卡片，才去处理对应的卡组；别的卡组不动。子空删子，父空只提示。**

## 📁 目录结构建议

```
./notes/
├─ math.md
├─ python.md
└─ linux.md
script.py
anki_export.tsv
```

## ❗ 注意事项

1. `id` 必须全局唯一，作为卡片主键，不要重复
2. Anki 必须保持打开，AnkiConnect 插件正常启用
3. 脚本删除卡片是 AnkiConnect 的删除笔记接口，删除后卡片不可恢复，建议先备份Anki库
4. 脚本不会自动新建笔记模型（Basic-md2anki / Cloze-md2anki），需要你手动在Anki提前建好
5. 标签用英文逗号分隔，前后空格会自动剔除

## 📤 输出文件

`anki_export.tsv`

- basic：uuid | front | back | deck
- cloze：uuid | text | extra | deck

## 📌 参数列表

| 参数 | 说明 |
| --- | --- |
| dir | 笔记md文件夹路径，默认 `./notes` |
| --toanki | 开启写入Anki；不加则仅预览 |
| --allow-delete | 开启删除&空卡组清理逻辑，谨慎使用 |
| --quiet | 安静模式，关闭debug日志 |

如果你想要，我可以再给一份精简版 README（适合放github，更短）。
